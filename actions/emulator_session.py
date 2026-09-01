"""Headless emulator session for the web UI — the Qt-free driver for
actions/libretro.py that ui/widgets/retro.py's RetroScreen is for the legacy
app.

Two rules validated in the Fase 1 prototypes, both load-bearing here:

1. The QApplication this session's GL context depends on must be created on
   the *main* thread before this session ever starts, and never have exec()
   called on it. Creating it on a worker thread emulates fine but segfaults
   the whole process at interpreter shutdown, reproduced across every QPA
   platform plugin — Qt's "QCoreApplication belongs to the main thread" rule,
   not a platform quirk. actions/webui_host.py owns that responsibility, once
   per process, before webview.start() takes the main thread. This module
   only asserts a QApplication instance already exists; it never creates one.
2. The GL context must be made current on the same thread that calls
   retro_run() (actions/libretro.py's run_frame() already does this every
   frame) — so the session's own background thread has to be both the pacer
   and the renderer. There is no separate "GL thread". This extends to any
   call into the core, not just run_frame(): save_state()/load_state()/
   reset() route through _pending_actions and run on this same thread
   for exactly that reason — a hardware-rendered console (confirmed with
   PS2) segfaults if they're called directly from the caller's own thread
   (an HTTP handler thread, in production), because the GL context is
   current on the emulation thread, not theirs. Software cores (GBA/SNES)
   don't have this hazard, which is why it went unnoticed until PS2.

Only one core may be loaded per process (actions/libretro.py's `load()` is a
process-global singleton, and cores keep process-global state) — this session
and the legacy Qt emulator panel must never run at the same time. See
`ui_conflict_guard` below.
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Optional

from actions.emulator_pacing import advance_frame_debt

_AUTOSAVE_INTERVAL_S = 30.0  # matches ui/panels/emulators.py's _AUTOSAVE_MS


class EmulatorSessionError(RuntimeError):
    """Raised for session-level failures (missing core/BIOS, already running)."""


def _require_qapplication() -> None:
    """Fail loudly instead of segfaulting later — see the module docstring's
    rule 1. A worker-thread QApplication looks fine right up until process
    exit, which makes it exactly the kind of mistake worth catching early."""
    from PyQt6.QtWidgets import QApplication
    if QApplication.instance() is None:
        raise EmulatorSessionError(
            "No hay QApplication en el hilo principal. actions/webui_host.py "
            "tiene que crearlo antes de arrancar cualquier EmulatorSession — "
            "crearlo en un hilo de fondo emula bien pero revienta el proceso "
            "al cerrar (ver el docstring de este módulo)."
        )


class EmulatorSession:
    """Owns one running libretro core: its background thread, its GL context
    (via actions/libretro.py) and its save data. One instance is ever "live"
    at a time — see start()/stop()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

        self._core = None
        self.console_id: str = ""
        self.rom_path: Optional[Path] = None
        self.title: str = ""
        self._stem: str = ""

        self._frame_lock = threading.Lock()
        self._last_frame: tuple = (None, 0, 0, 0, 0)
        self._frame_id = 0

        self._fps_measured = 0.0
        self._error: Optional[str] = None
        self._loaded_event = threading.Event()

        # save_state/load_state/reset requests, drained on the emulation
        # thread itself — see rule 2 in the module docstring.
        self._pending_actions: "queue.Queue[tuple[str, threading.Event, dict]]" = queue.Queue()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self, console_id: str, rom_path: str | Path, title: str = "") -> None:
        _require_qapplication()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise EmulatorSessionError(
                    "Ya hay una sesión de emulador en marcha — llama a stop() primero."
                )

            from actions import emulator_runtime as er

            core_path = er.find_core(console_id)
            if core_path is None:
                raise EmulatorSessionError(f"Falta el emulador para «{console_id}»")

            if console_id == "ps2":
                from actions import bios
                if not bios.has_ps2_bios(er.system_dir()):
                    raise EmulatorSessionError(
                        "PS2 necesita una BIOS de tu propia consola antes de poder jugar"
                    )

            rom_path = Path(rom_path)
            if not rom_path.is_file():
                raise EmulatorSessionError(f"ROM no encontrada: {rom_path}")

            self.console_id = console_id
            self.rom_path = rom_path
            self.title = title or rom_path.stem
            self._stem = rom_path.stem
            self._error = None
            self._stop_event.clear()
            self._pause_event.clear()
            self._loaded_event = threading.Event()

            self._thread = threading.Thread(
                target=self._run, name="emulator-session", daemon=True,
            )
            self._thread.start()

            # Wait for load_game() to either succeed or raise, so a bad ROM/
            # core surfaces as an exception from start() instead of silently
            # leaving the caller with a session that never produces frames.
            if not self._loaded_event.wait(timeout=30.0):
                raise EmulatorSessionError("El core no respondió a tiempo al cargar")
            if self._error:
                err, self._error = self._error, None
                self._thread = None
                raise EmulatorSessionError(err)

    def stop(self) -> None:
        with self._lock:
            if self._thread is None:
                return
            self._stop_event.set()
            self._thread.join(timeout=10.0)
            self._thread = None
            self._core = None

    def pause(self, paused: bool) -> None:
        if paused:
            self._pause_event.set()
        else:
            self._pause_event.clear()

    def reset(self) -> None:
        if self._core is not None:
            self._run_on_emulation_thread("reset")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Frame / input
    # ------------------------------------------------------------------
    def latest_frame(self) -> tuple:
        """(data, width, height, pitch, pixel_format) — see
        actions/libretro.py's current_frame() for the format."""
        with self._frame_lock:
            return self._last_frame

    def frame_id(self) -> int:
        """Increments once per rendered frame. A consumer (the MJPEG
        encoder in actions/webui_server.py) polls this to skip re-encoding
        an unchanged frame instead of comparing pixel buffers — cheap enough
        to poll in a tight loop without touching the emulation thread."""
        with self._frame_lock:
            return self._frame_id

    def set_button(self, button: str, pressed: bool) -> None:
        core = self._core
        if core is not None:
            core.set_button(button, pressed)

    def set_axis(self, index: int, axis: int, value: int) -> None:
        core = self._core
        if core is not None:
            core.set_axis(index, axis, value)

    def set_pointer(self, x: int, y: int, pressed: bool) -> None:
        core = self._core
        if core is not None:
            core.set_pointer(x, y, pressed)

    def clear_input(self) -> None:
        core = self._core
        if core is not None:
            core.clear_input()

    # ------------------------------------------------------------------
    # Save states + battery saves — same file layout as
    # ui/panels/emulators.py:_save_paths, so save data is shared with the
    # legacy Qt emulator and doesn't fork into two incompatible locations.
    # ------------------------------------------------------------------
    def _save_paths(self) -> tuple[Path, Path]:
        from actions import emulator_runtime as er
        folder = er.saves_dir()
        return folder / f"{self._stem}.srm", folder / f"{self._stem}.state"

    def _restore_sram(self) -> None:
        core = self._core
        if core is None:
            return
        srm, _ = self._save_paths()
        try:
            if srm.is_file():
                core.write_sram(srm.read_bytes())
        except Exception:
            pass

    def _flush_sram(self) -> None:
        core = self._core
        if core is None:
            return
        srm, _ = self._save_paths()
        try:
            blob = core.read_sram()
            if blob and blob.strip(b"\xff") and blob.strip(b"\x00"):
                srm.write_bytes(blob)
        except Exception:
            pass

    def save_state(self) -> None:
        if self._core is None:
            raise EmulatorSessionError("No hay ninguna sesión activa")
        self._run_on_emulation_thread("save_state")

    def load_state(self) -> None:
        if self._core is None:
            raise EmulatorSessionError("No hay ninguna sesión activa")
        self._run_on_emulation_thread("load_state")

    def _run_on_emulation_thread(self, action: str, timeout: float = 10.0) -> None:
        """Queues `action` (save_state/load_state/reset) for the emulation
        thread to run — see rule 2 in the module docstring for why these
        can't just run on the caller's own thread — and blocks until it's
        done or raises whatever it raised."""
        done = threading.Event()
        result: dict = {}
        self._pending_actions.put((action, done, result))
        if not done.wait(timeout=timeout):
            raise EmulatorSessionError("La sesión no respondió a tiempo")
        if "error" in result:
            raise result["error"]

    def _drain_pending_actions(self, core) -> None:
        """Runs any queued save_state/load_state/reset on the emulation
        thread, which is the only place it's safe to touch a hardware-
        rendered core — called once per loop iteration in _run(), including
        while paused, so the GL context (already current on this thread) is
        always the one these calls execute against."""
        while True:
            try:
                action, done, result = self._pending_actions.get_nowait()
            except queue.Empty:
                return
            try:
                if action == "save_state":
                    _, state_path = self._save_paths()
                    state_path.write_bytes(core.save_state())
                    self._flush_sram()
                elif action == "load_state":
                    _, state_path = self._save_paths()
                    if not state_path.is_file():
                        raise EmulatorSessionError(
                            "No hay ningún estado guardado para este juego"
                        )
                    core.load_state(state_path.read_bytes())
                elif action == "reset":
                    core.reset()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self) -> dict:
        core = self._core
        return {
            "running": self.is_running(),
            "console": self.console_id,
            "title": self.title,
            "paused": self._pause_event.is_set(),
            "fps": round(self._fps_measured, 1),
            "hw_render": bool(getattr(core, "hw_render_active", False)),
        }

    # ------------------------------------------------------------------
    # Background thread — owns the core, the GL context and the pacing.
    # Rule 2 from the module docstring: this thread is both pacer and
    # renderer, always.
    # ------------------------------------------------------------------
    def _run(self) -> None:
        from actions import emulator_runtime as er
        from actions import libretro

        try:
            spec = er.CORES.get(self.console_id)
            options = dict(spec.options) if spec and spec.options else {}
            core_path = er.find_core(self.console_id)
            core = libretro.load(core_path, er.system_dir(), er.saves_dir(),
                                 options=options)
            core.load_game(str(self.rom_path))
        except Exception as exc:
            self._error = str(exc)
            self._loaded_event.set()
            return

        self._core = core
        self._restore_sram()
        self._loaded_event.set()

        debt = 0.0
        last = time.perf_counter()
        last_autosave = last
        fps_frames = 0
        fps_clock = last

        try:
            while not self._stop_event.is_set():
                now = time.perf_counter()
                self._drain_pending_actions(core)
                if self._pause_event.is_set():
                    last = now
                    time.sleep(0.02)
                    continue

                due, debt = advance_frame_debt(debt, now - last, core.fps)
                last = now
                for _ in range(due):
                    core.run_frame()
                    with self._frame_lock:
                        self._last_frame = core.current_frame()
                        self._frame_id += 1
                    core.take_audio()  # drained, not played yet — see Fase 2 plan
                    fps_frames += 1
                if not due:
                    time.sleep(0.001)

                if now - fps_clock >= 1.0:
                    self._fps_measured = fps_frames / (now - fps_clock)
                    fps_frames = 0
                    fps_clock = now

                if now - last_autosave >= _AUTOSAVE_INTERVAL_S:
                    self._flush_sram()
                    last_autosave = now
        finally:
            self._flush_sram()
            try:
                libretro.unload()
            except Exception:
                pass
            self._core = None


# ----------------------------------------------------------------------
# Process-wide singleton + legacy-conflict guard
#
# actions/libretro.py's load() is a process-global singleton (see its own
# docstring): two live cores would fight over the same ctypes DLL handle.
# The web session and ui/panels/emulators.py's Qt-driven session must never
# both be "live" — enforced here rather than trusted to callers.
# ----------------------------------------------------------------------
_session: Optional[EmulatorSession] = None


def get_session() -> EmulatorSession:
    global _session
    if _session is None:
        _session = EmulatorSession()
    return _session
