"""Headless YouTube Music player using mpv + yt-dlp.

This module provides a minimal controller interface Jarvis can call to play/pause/seek
without opening a GUI. mpv must be installed on the system and `yt-dlp` available
on PATH (we installed the package; PATH may need update).

API:
  play(query)
  pause()
  resume()
  toggle_play()
  stop()
  volume(level)
  seek(seconds)
  current() -> dict (title, artists, position, duration, playing)

This implementation uses mpv JSON IPC via named pipe on Windows: \\.\\pipe\\jarvis_mpv
It attempts to start mpv if not found. If mpv is not installed, functions will return
informative errors and Jarvis can fallback to the GUI-based integration.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from actions.paths import RESOURCE_DIR

# ── music log ─────────────────────────────────────────────────────────────────
_LOG_PREFIX = "\033[36m[MUSIC]\033[0m"  # cyan
# Toggle music subsystem logging. Default: disabled to avoid verbose logs filling disk.
_MUSIC_LOG_ENABLED: bool = False

def _log(*parts):
    """Internal logger for the music module.
    When _MUSIC_LOG_ENABLED is False this becomes a no-op to avoid noisy output.
    """
    if not _MUSIC_LOG_ENABLED:
        return
    ts = time.strftime("%H:%M:%S")
    msg = f"{_LOG_PREFIX} {ts} " + " ".join(str(p) for p in parts)
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Console codec (e.g. cp1252) can't render some glyphs — strip to ASCII
        enc = (getattr(sys.stdout, "encoding", None) or "ascii")
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def set_music_logging(enabled: bool) -> str:
    """Public helper to enable/disable music logging at runtime.

    Example:
        from actions.ytmusic_headless import set_music_logging
        set_music_logging(True)
    """
    global _MUSIC_LOG_ENABLED
    _MUSIC_LOG_ENABLED = bool(enabled)
    return f"Music logging {'enabled' if _MUSIC_LOG_ENABLED else 'disabled'}."
# ──────────────────────────────────────────────────────────────────────────────

# Two named-pipe slots for true simultaneous crossfade.
# Playback alternates between slot 0 and slot 1 on each crossfade transition.
# Per-process names: two Jarvis instances (dev + installed) must not attach to
# each other's mpv processes through a shared pipe.
_PIPE_PATHS  = [rf"\\.\pipe\jarvis_mpv_{os.getpid()}",
                rf"\\.\pipe\jarvis_mpv2_{os.getpid()}"]
_PIPE_PATH   = _PIPE_PATHS[0]  # kept as alias for external code
_MPV_EXE = "mpv"

# Simple playlist: stores list of {videoId, title, artists} and current index
_playlist: list = []
_playlist_idx: int = 0
_autoplay_enabled: bool = True
_autoplay_thread: Optional[threading.Thread] = None
_autoplay_last_switch: float = 0.0
_job_handle = None

# Crossfade: fade-out the last N seconds of a track, fade-in the next one.
# 0 = disabled.
_crossfade_secs: int = 3
_crossfade_enabled: bool = False   # off by default; toggled via set_crossfade()
_crossfade_on_skip: bool = False   # apply crossfade also on manual next/previous
# Starting the alternate MPV process and opening its audio output takes time,
# even when the stream URL has already been resolved.  Begin preparation this
# far ahead of the audible overlap so the outgoing song is still playing while
# the incoming one becomes ready.
_CROSSFADE_PREPARE_SECONDS: float = 8.0
_user_volume: int = 100            # user's intended volume (0-100)
_crossfade_fading_out: bool = False
import atexit as _atexit

def _cleanup_on_exit():
    global _shutting_down, _job_handle
    _shutting_down = True
    for slot in range(2):
        try:
            p = _procs[slot]
            if p is not None and p.poll() is None:
                if slot == _active_slot:
                    _send_command(["quit"], pipe=_PIPE_PATHS[slot])
                try:
                    p.wait(timeout=2)
                except Exception:
                    pass
                if p.poll() is None:
                    p.terminate()
            _procs[slot] = None
        except Exception:
            pass
    try:
        if _job_handle is not None:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_job_handle)
            _job_handle = None
    except Exception:
        pass

_atexit.register(_cleanup_on_exit)
# Try to locate mpv.exe in the workspace root or tools folder, prefer that over PATH
def _locate_mpv() -> str:
    # workspace/resource root (handles PyInstaller frozen builds too)
    root = RESOURCE_DIR
    candidates = [
        root / 'mpv.exe',
        root / 'tools' / 'mpv' / 'mpv.exe',
        root / 'tools' / 'mpv.exe',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # fallback to PATH
    import shutil
    exe = shutil.which('mpv') or shutil.which('mpv.exe')
    return exe or 'mpv'

# initialize _MPV_EXE to located path
_MPV_EXE = _locate_mpv()

_last_meta = {
    "title": "",
    "artists": "",
    "videoId": "",
    "duration": 0,
    "position": 0,
    "playing": False,
    "ready": False,
    "buffering": False,
    "state": "stopped",
    "_sampled_at": 0.0,
    "_started": False,
}
_procs: list = [None, None]   # one subprocess per slot
_active_slot: int = 0         # which slot is currently the "main" player
_xfade_in_progress: bool = False  # True while crossfade overlap thread is running

# Convenience accessors (always read the current _active_slot value)
def _cur_proc():  return _procs[_active_slot]
def _alt_proc():  return _procs[1 - _active_slot]
def _cur_pipe():  return _PIPE_PATHS[_active_slot]
def _alt_pipe():  return _PIPE_PATHS[1 - _active_slot]
_lock = threading.Lock()
_mpv_start_locks = [threading.Lock() for _ in _PIPE_PATHS]
_shutting_down = False
_STREAM_TTL_SECONDS = 60 * 30  # 30 min — YouTube CDN URLs expire sooner than 2 h in practice
_stream_cache: dict[str, dict] = {}
_stream_loading: set[str] = set()
_stream_lock = threading.Lock()

# Cap how many times we re-resolve/reload a single track when playback won't
# advance. A frozen position with no EOF is usually NOT an expired URL but the
# audio device stalling (e.g. a Bluetooth headset forced into hands-free/HFP
# mode because the mic is open) — reloading can't fix that, so we stop and warn.
_MAX_RELOADS_PER_TRACK = 2
_reload_counts: dict[str, int] = {}
_reload_givenup: set[str] = set()
# Tracks for which we already tried resetting the audio output to the system
# default after a stall (so we attempt that recovery at most once per track).
_audio_reset_tried: set[str] = set()
_MAX_PROGRESS_EXTRAPOLATION = 2.0
_AUDIO_OPEN_GRACE_SECONDS = 6.0


def _reset_reload_guard() -> None:
    _reload_counts.clear()
    _reload_givenup.clear()
    _audio_reset_tried.clear()


def _mpv_available() -> bool:
    try:
        subprocess.run([_MPV_EXE, "--version"], capture_output=True, timeout=3)
        return True
    except Exception:
        return False


def _disable_win_audio_ducking(disable: bool = True):
    """Control Windows audio ducking (the feature that lowers music volume when
    a communication app like a microphone is active). Stored in registry under
    HKCU\\SOFTWARE\\Microsoft\\Multimedia\\Audio\\UserDuckingPreference:
      0=mute others, 1=reduce 80% (default), 2=reduce 50%, 3=do nothing.
    disable=True writes 3 (no ducking); disable=False restores 1 (Windows default)."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Multimedia\Audio",
            0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY,
        )
        winreg.SetValueEx(key, "UserDuckingPreference", 0, winreg.REG_DWORD, 3 if disable else 1)
        winreg.CloseKey(key)
    except Exception:
        pass


def set_ducking(disable: bool = True) -> str:
    """Public toggle for Windows audio ducking suppression."""
    _disable_win_audio_ducking(bool(disable))
    return f"Atenuación de audio de Windows {'desactivada' if disable else 'restaurada'}."


# Preferred audio quality for yt-dlp resolution.
_audio_quality: str = "m4a"   # "best" | "m4a" | "opus" | "low"
_QUALITY_FORMATS = {
    # No ext filter: yt-dlp picks the highest-bitrate audio available
    # (typically Opus ~160 kbps, or AAC/Opus 256 kbps when YouTube offers it).
    "best": "bestaudio/best",
    "m4a":  "bestaudio[ext=m4a]/bestaudio/best",
    "opus": "bestaudio[ext=webm]/bestaudio/best",
    "low":  "worstaudio[ext=m4a]/worstaudio/bestaudio/best",
}


def _format_for_quality() -> str:
    return _QUALITY_FORMATS.get(_audio_quality, _QUALITY_FORMATS["m4a"])


def set_audio_quality(quality: str = "m4a") -> str:
    """Set the preferred audio quality/format used when resolving streams."""
    global _audio_quality
    q = str(quality or "m4a").lower()
    if q not in _QUALITY_FORMATS:
        q = "m4a"
    _audio_quality = q
    # Drop cached durations so next play fetches a fresh one at new quality.
    with _stream_lock:
        _stream_cache.clear()
    # Propagate new ytdl-format to running mpv slots via script-opts.
    fmt = _format_for_quality()
    for slot in range(len(_PIPE_PATHS)):
        p = _procs[slot]
        if p is not None and p.poll() is None:
            _send_command(
                ["change-list", "script-opts", "append", f"ytdl_hook-ytdl_format={fmt}"],
                pipe=_PIPE_PATHS[slot],
            )
    labels = {
        "best": "Máxima disponible (≈160–256 kbps)",
        "m4a": "AAC-LC ~128 kbps (M4A)",
        "opus": "Opus ~160 kbps (WebM)",
        "low": "AAC ~48 kbps (M4A, ahorro de datos)",
    }
    return f"Calidad de audio: {labels.get(q, q)}."


# Preferred mpv audio output device ("" = mpv autoselect / system default).
_audio_device: str = ""
_audio_device_lock = threading.RLock()


def list_audio_output_devices() -> list[dict]:
    """Enumerate mpv audio output devices: [{'name','description'}].
    First entry is always the automatic/default option ('')."""
    out = [{"name": "", "description": "Automático (predeterminado del sistema)"}]
    try:
        r = subprocess.run([_MPV_EXE, "--audio-device=help"],
                           capture_output=True, text=True, timeout=6)
        import re as _re
        for line in (r.stdout or "").splitlines():
            m = _re.match(r"\s*'([^']+)'\s*\((.*)\)\s*$", line)
            if not m:
                continue
            name, desc = m.group(1), m.group(2)
            if name in ("auto",):       # already covered by our "" default
                continue
            out.append({"name": name, "description": desc})
    except Exception as e:
        _log(f"list_audio_output_devices error: {e}")
    return out


def set_audio_output_device(name: str = "") -> str:
    """Choose and validate the music output, confirming every live MPV IPC."""
    global _audio_device
    requested = str(name or "").strip()
    if requested == "auto":
        requested = ""

    with _audio_device_lock:
        invalid_requested = False
        # Auto is always a valid MPV target. Avoid spawning ``mpv --help`` on
        # the hotplug recovery path, where blocking the poller would freeze the
        # UI while the endpoint is changing.
        if requested:
            devices = list_audio_output_devices()
            available = {
                str(item.get("name") or "").strip()
                for item in devices
                if isinstance(item, dict)
            }
            if requested not in available:
                _log(f"Salida '{requested}' no disponible; usando auto")
                invalid_requested = True
                requested = ""

        live_slots = [
            slot for slot, proc in enumerate(_procs)
            if proc is not None and proc.poll() is None
        ]
        target = requested or "auto"
        failed = False
        for slot in live_slots:
            response = _ipc_request(
                ["set_property", "audio-device", target],
                pipe=_PIPE_PATHS[slot],
            )
            if not response or response.get("error") != "success":
                failed = True
                break

        if failed:
            # A successful property write only schedules AO reconfiguration. If
            # one slot rejects it, move every live slot back to auto and force
            # the existing AO to reopen; the readiness poller validates the end
            # result instead of trusting these ACKs.
            for slot in live_slots:
                pipe = _PIPE_PATHS[slot]
                _ipc_request(["set_property", "audio-device", "auto"], pipe=pipe)
                _ipc_request(["ao-reload"], pipe=pipe)
            _audio_device = ""
        elif invalid_requested:
            # The command above moved each live slot to auto. Force an
            # immediate reopen as well: a stale WASAPI endpoint can otherwise
            # leave the old AO disabled until the next file is loaded.
            for slot in live_slots:
                _ipc_request(["ao-reload"], pipe=_PIPE_PATHS[slot])
            _audio_device = ""
        else:
            _audio_device = requested

        try:
            from actions import app_settings
            app_settings.set("audio_output_device", _audio_device)
        except Exception:
            pass

        if live_slots:
            with _lock:
                if _last_meta.get("videoId") and _last_meta.get("playing"):
                    _last_meta.update({
                        "ready": False,
                        "buffering": False,
                        "state": "loading",
                        "_started": False,
                    })

        effective = _audio_device or "auto"
        _log(f"Salida de audio → {effective}")
        if failed:
            return "No se pudo abrir esa salida; usando Automático."
        if invalid_requested:
            return "La salida seleccionada ya no está disponible; usando Automático."
        return f"Salida de audio: {effective}."


def _reconcile_audio_output_device_list(devices) -> bool:
    """Fallback once when a pinned endpoint disappears from MPV's live list."""
    active = str(_audio_device or "").strip()
    if not active:
        return False
    names = {
        str(item.get("name") or "").strip()
        for item in (devices or [])
        if isinstance(item, dict)
    }
    if active in names:
        return False
    _log(f"Salida desconectada ({active}); cambiando a auto")
    set_audio_output_device("")
    return True


# ── Graphic equalizer (10-band, applied via mpv's ffmpeg audio filter) ──────
_EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
_eq_enabled: bool = False
_eq_gains: list = [0] * len(_EQ_BANDS)   # dB per band, -12..+12


def _build_eq_af() -> str:
    """Build the mpv 'af' filter string for the current EQ, or '' to disable."""
    if not _eq_enabled:
        return ""
    parts = []
    for freq, gain in zip(_EQ_BANDS, _eq_gains):
        g = int(gain)
        if g != 0:
            parts.append(f"equalizer=f={freq}:width_type=o:width=1:g={g}")
    if not parts:
        return ""
    return "lavfi=[" + ",".join(parts) + "]"


def _apply_eq_to_slot(slot: int) -> None:
    p = _procs[slot]
    if p is not None and p.poll() is None:
        _send_command(["set_property", "af", _build_eq_af()], pipe=_PIPE_PATHS[slot])


def set_equalizer(enabled=None, gains=None) -> str:
    """Enable/disable and/or set the 10-band EQ gains (dB, -12..+12).
    Applies live to all running mpv slots."""
    global _eq_enabled, _eq_gains
    if enabled is not None:
        _eq_enabled = bool(enabled)
    if gains is not None:
        try:
            g = [int(x) for x in list(gains)][:len(_EQ_BANDS)]
        except Exception:
            g = []
        g += [0] * (len(_EQ_BANDS) - len(g))
        _eq_gains = [max(-12, min(12, x)) for x in g]
    for slot in range(len(_PIPE_PATHS)):
        _apply_eq_to_slot(slot)
    return f"Ecualizador {'activado' if _eq_enabled else 'desactivado'}."


def get_equalizer() -> dict:
    return {"enabled": _eq_enabled, "gains": list(_eq_gains), "bands": list(_EQ_BANDS)}


def _create_windows_job_for_child(proc: subprocess.Popen) -> bool:
    """Attach mpv to a Windows Job Object so it is killed when Jarvis exits.

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE makes the OS terminate all processes in
    the job when the last handle to the job object is closed — which happens
    automatically when the parent process exits, even if killed forcefully.
    """
    global _job_handle
    if os.name != "nt":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wt.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        kernel32.SetInformationJobObject.argtypes = [
            wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD
        ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit",     ctypes.c_longlong),
                ("LimitFlags",             wt.DWORD),
                ("MinimumWorkingSetSize",  ctypes.c_size_t),
                ("MaximumWorkingSetSize",  ctypes.c_size_t),
                ("ActiveProcessLimit",     wt.DWORD),
                ("Affinity",              ctypes.c_size_t),
                ("PriorityClass",         wt.DWORD),
                ("SchedulingClass",       wt.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount",  ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount",   ctypes.c_ulonglong),
                ("WriteTransferCount",  ctypes.c_ulonglong),
                ("OtherTransferCount",  ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo",               IO_COUNTERS),
                ("ProcessMemoryLimit",   ctypes.c_size_t),
                ("JobMemoryLimit",       ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed",    ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation   = 9

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            return False

        proc_handle = wt.HANDLE(int(proc._handle))
        ok = kernel32.AssignProcessToJobObject(job, proc_handle)
        if not ok:
            kernel32.CloseHandle(job)
            return False

        _job_handle = job
        return True
    except Exception:
        return False


def _locate_ytdlp() -> Optional[str]:
    import shutil

    candidates = [
        str(RESOURCE_DIR / "yt-dlp.exe"),  # bundled binary (frozen build)
        shutil.which("yt-dlp"),
        shutil.which("yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _wait_for_pipe(pipe_path: str, timeout_ms: int = 5000) -> bool:
    """Wait until mpv's named pipe is ready using WaitNamedPipe (Windows API)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            ok = kernel32.WaitNamedPipeW(pipe_path, 500)
            if ok:
                return True
            err = kernel32.GetLastError()
            if err == 2:  # ERROR_FILE_NOT_FOUND: pipe server not up yet
                time.sleep(0.1)
                continue
            if err == 0 or err == 231:  # 231 = ERROR_PIPE_BUSY (pipe exists but busy)
                time.sleep(0.05)
                continue
            # Any other error: pipe might be ready, try anyway
            return True
        return False
    except Exception:
        # ctypes unavailable: fallback to time-based wait
        time.sleep(1.5)
        return True


def _start_mpv(slot: int | None = None) -> bool:
    """Start the mpv process for the given slot (default: active slot)."""
    if slot is None:
        slot = _active_slot
    if slot < 0 or slot >= len(_procs):
        return False
    # Warmup, prefetch and a user click can arrive on different threads. Keep
    # the process unpublished to other starters until its IPC pipe is ready.
    with _mpv_start_locks[slot]:
        return _start_mpv_locked(slot)


def _start_mpv_locked(slot: int) -> bool:
    if _shutting_down:
        return False
    if _procs[slot] is not None and _procs[slot].poll() is None:
        return True
    if not _mpv_available():
        _log(f"ERROR: mpv no encontrado")
        return False
    pipe = _PIPE_PATHS[slot]
    _log(f"Arrancando mpv slot={slot} pipe={pipe}")
    env = os.environ.copy()
    venv_scripts = str(Path(sys.executable).parent)
    env['PATH'] = venv_scripts + os.pathsep + env.get('PATH', '')
    _disable_win_audio_ducking()
    # Log file for mpv stderr so we can diagnose audio/stream issues
    try:
        from actions.paths import DATA_DIR
        mpv_log_path = str(DATA_DIR / "logs" / f"mpv_slot{slot}.log")
        os.makedirs(str(DATA_DIR / "logs"), exist_ok=True)
    except Exception:
        mpv_log_path = None
    args = [
        _MPV_EXE,
        "--no-video",
        "--idle=yes",
        f"--input-ipc-server={pipe}",
        "--force-window=no",
        f"--ytdl-format={_format_for_quality()}",
        "--cache=yes",
        "--cache-pause=yes",         # pause only if the stream actually underruns
        "--cache-pause-wait=0.5",    # recover from a short underrun promptly
        # Do not make the first audible sample wait for a full cache window.
        # The readiness contract/UI already freezes the clock while MPV reports
        # buffering, so this can start as soon as the decoder and AO are open.
        "--cache-pause-initial=no",
        "--demuxer-readahead-secs=10",
        "--audio-exclusive=no",       # force WASAPI shared mode (don't grab device exclusively)
        f"--user-agent={_YOUTUBE_STREAM_USER_AGENT}",
    ]
    if _audio_device:
        args.append(f"--audio-device={_audio_device}")
    # Only instruct mpv to write a log file when music logging is enabled —
    # this avoids large mpv_slot*.log files filling disk by default.
    if mpv_log_path and _MUSIC_LOG_ENABLED:
        args.append(f"--log-file={mpv_log_path}")
        _log(f"mpv log → {mpv_log_path}")
    ytdlp_path = _locate_ytdlp()
    if ytdlp_path:
        args.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp_path}")
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        _create_windows_job_for_child(proc)
        ready = _wait_for_pipe(pipe, timeout_ms=6000)
        _log(f"mpv slot={slot} {'listo ✓' if ready else 'TIMEOUT esperando pipe'}")
        if not ready or _shutting_down:
            try:
                proc.terminate()
            except Exception:
                pass
            _procs[slot] = None
            return False
        # Publish only after IPC is usable. Setters and playback commands can
        # otherwise observe a live process whose named pipe does not exist yet.
        _procs[slot] = proc
        if _eq_enabled:
            _apply_eq_to_slot(slot)
        return True
    except Exception as e:
        _log(f"ERROR arrancando mpv slot={slot}: {e}")
        _procs[slot] = None
        return False


def _parse_mpv_response(raw: bytes) -> Optional[dict]:
    """Parse mpv response bytes: finds the JSON line that is a command reply (has 'error' key)."""
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "error" in obj:
                return obj
        except Exception:
            pass
    return None


def _ipc_request(cmd: list, request_id: int = 1, pipe: str | None = None) -> Optional[dict]:
    """Send a JSON IPC command to mpv and return the response dict, or None on failure.
    Works with pywin32 (win32file) OR via ctypes (no extra deps)."""
    pipe_path = pipe if pipe is not None else _cur_pipe()
    payload = (json.dumps({"command": cmd, "request_id": request_id}) + "\n").encode("utf-8")

    # Method 1: win32file / pywin32
    try:
        import win32file  # type: ignore
        handle = win32file.CreateFile(
            pipe_path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None
        )
        try:
            win32file.WriteFile(handle, payload)
            _, data = win32file.ReadFile(handle, 65536)
        finally:
            win32file.CloseHandle(handle)
        return _parse_mpv_response(data)
    except ImportError:
        pass  # fall through to ctypes
    except Exception:
        return None

    # Method 2: ctypes (works without pywin32)
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        GENERIC_READ  = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        k32.CreateFileW.restype = ctypes.c_void_p
        h = k32.CreateFileW(pipe_path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if h is None or ctypes.c_void_p(h).value in (None, -1):
            return None
        try:
            bw = wt.DWORD(0)
            if not k32.WriteFile(h, payload, len(payload), ctypes.byref(bw), None):
                return None
            buf = ctypes.create_string_buffer(65536)
            br  = wt.DWORD(0)
            if not k32.ReadFile(h, buf, 65536, ctypes.byref(br), None):
                return None
            return _parse_mpv_response(buf.raw[: br.value])
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _send_command(cmd: list, pipe: str | None = None) -> bool:
    """Send a fire-and-forget IPC command. Retries up to 5 times on failure."""
    pipe_path = pipe if pipe is not None else _cur_pipe()
    payload = json.dumps({"command": cmd}) + "\n"
    for attempt in range(5):
        try:
            with open(pipe_path, "w+b", buffering=0) as p:
                p.write(payload.encode("utf-8"))
            return True
        except Exception:
            if attempt < 4:
                time.sleep(0.3)
    _log(f"WARN: _send_command falló tras 5 intentos cmd={cmd[0] if cmd else '?'} pipe={pipe_path}")
    return False


def _get_mpv_property(prop: str, pipe: str | None = None):
    """Read a property from mpv via IPC. Returns value or None."""
    resp = _ipc_request(["get_property", prop], pipe=pipe)
    if resp and resp.get("error") == "success":
        return resp.get("data")
    return None


_PLAYBACK_PROPERTIES = (
    "time-pos",
    "duration",
    "pause",
    "eof-reached",
    "idle-active",
    "core-idle",
    "paused-for-cache",
    "seeking",
    "audio-out-params",
    "current-ao",
)


def _read_mpv_playback_state(pipe: str | None = None) -> dict:
    """Return one coherent-enough snapshot of the MPV properties we expose.

    JSON IPC does not provide an atomic multi-property read, but keeping the
    reads together makes the readiness contract explicit and testable.
    """
    if pipe is None:
        return {name: _get_mpv_property(name) for name in _PLAYBACK_PROPERTIES}
    return {name: _get_mpv_property(name, pipe=pipe) for name in _PLAYBACK_PROPERTIES}


def _mpv_audio_ready(snapshot: dict) -> bool:
    """True once an audio-only file has a real output, including at time 0.0."""
    if bool(snapshot.get("idle-active")) or bool(snapshot.get("eof-reached")):
        return False
    if snapshot.get("time-pos") is None:
        return False
    params = snapshot.get("audio-out-params")
    if not isinstance(params, dict) or not params:
        return False
    current_ao = str(snapshot.get("current-ao") or "").strip().lower()
    return bool(current_ao and current_ao != "null")


def _derive_playback_phase(
    snapshot: dict,
    *,
    was_ready: bool = False,
    requested_playing: bool = True,
) -> dict:
    """Translate MPV's low-level properties into the public playback contract."""
    output_ready = _mpv_audio_ready(snapshot)
    ready = bool(was_ready or output_ready)
    paused = bool(snapshot.get("pause"))
    eof = bool(snapshot.get("eof-reached"))
    idle = bool(snapshot.get("idle-active"))
    playing = bool(requested_playing and not paused and not eof)
    if idle and ready:
        playing = False

    output_missing = ready and not output_ready
    buffering = bool(
        playing
        and ready
        and (
            snapshot.get("paused-for-cache")
            or snapshot.get("seeking")
            or snapshot.get("core-idle")
            or output_missing
        )
    )
    progressing = bool(playing and ready and not buffering)

    if playing and not ready:
        state = "loading"
    elif buffering:
        state = "buffering"
    elif progressing:
        state = "playing"
    elif ready and paused:
        state = "paused"
    else:
        state = "stopped"

    return {
        "playing": playing,
        "ready": ready,
        "buffering": buffering,
        "state": state,
        "progressing": progressing,
    }


def _apply_mpv_playback_state(snapshot: dict, *, update_media: bool = True) -> dict:
    """Apply an MPV snapshot to shared metadata under one lock acquisition."""
    with _lock:
        was_ready = bool(_last_meta.get("ready", _last_meta.get("_started", False)))
        paused = snapshot.get("pause")
        requested_playing = (
            bool(_last_meta.get("playing", False))
            if paused is None
            else not bool(paused)
        )
        phase = _derive_playback_phase(
            snapshot,
            was_ready=was_ready,
            requested_playing=requested_playing,
        )
        if update_media:
            pos = snapshot.get("time-pos")
            dur = snapshot.get("duration")
            if pos is not None:
                _last_meta["position"] = float(pos)
                _last_meta["_sampled_at"] = time.monotonic()
            if dur is not None:
                _last_meta["duration"] = float(dur)
        _last_meta.update({
            "playing": phase["playing"],
            "ready": phase["ready"],
            "buffering": phase["buffering"],
            "state": phase["state"],
            "_started": phase["ready"],
        })
    return phase


def _wait_for_audio_ready(
    pipe: str | None = None,
    *,
    timeout: float = 12.0,
    poll_interval: float = 0.1,
) -> bool:
    """Condition-based wait used by crossfade; position 0.0 remains valid."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while not _shutting_down and time.monotonic() < deadline:
        snapshot = _read_mpv_playback_state(pipe=pipe)
        if _derive_playback_phase(
            snapshot,
            was_ready=False,
            requested_playing=True,
        )["progressing"]:
            return True
        time.sleep(max(0.01, float(poll_interval)))
    return False


def _wait_for_audio_output_ready(
    pipe: str | None = None,
    *,
    timeout: float = 12.0,
    poll_interval: float = 0.1,
) -> bool:
    """Wait until MPV has an AO for a paused/preloaded track."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while not _shutting_down and time.monotonic() < deadline:
        snapshot = _read_mpv_playback_state(pipe=pipe)
        if _mpv_audio_ready(snapshot) and not bool(snapshot.get("seeking")):
            return True
        time.sleep(max(0.01, float(poll_interval)))
    return False


# Extra args prepended to every yt-dlp invocation. Kept empty: the default
# client selection resolves and plays correctly with an up-to-date yt-dlp.
# (Forcing player_client=tv/web/web_safari breaks with "Video unavailable" /
# "format not available" / "DRM protected" because those clients need PO tokens.)
_YTDLP_EXTRACTOR_ARGS: list = []
_YOUTUBE_STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)


def _ytdlp_cmd(args: list) -> Optional[str]:
    """Run yt-dlp with given args; returns stdout on success, None on failure."""
    exe = _locate_ytdlp()
    bases = [exe] if exe else []
    # `python -m yt_dlp` only works when sys.executable is a real interpreter.
    # In the frozen build sys.executable IS Jarvis.exe, so "-m yt_dlp" would
    # just relaunch the whole app instead of running yt-dlp.
    if not getattr(sys, "frozen", False):
        bases.append([sys.executable, "-m", "yt_dlp"])
    for base in bases:
        cmd = (base if isinstance(base, list) else [base]) + _YTDLP_EXTRACTOR_ARGS + args
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return None


def _get_stream_url_and_duration(url: str) -> tuple[Optional[str], int]:
    """Returns (stream_url, duration_seconds). duration may be 0 on failure."""
    # Force audio-only stream at the user's preferred quality
    out = _ytdlp_cmd(["--format", _format_for_quality(),
                      "--print", "%(url)s\t%(duration)s", "--no-playlist", url])
    if out:
        parts = out.splitlines()[0].split("\t")
        if len(parts) == 2:
            stream, dur_s = parts[0].strip(), parts[1].strip()
            try:
                return stream, int(float(dur_s))
            except Exception:
                return stream, 0
    # Fallback: just get URL with -g
    out2 = _ytdlp_cmd(["--format", "bestaudio/best", "-g", "--no-playlist", url])
    if out2:
        return out2.splitlines()[0].strip(), 0
    return None, 0


# Keep backward-compatible alias
def _get_stream_url(url: str) -> Optional[str]:
    s, _ = _get_stream_url_and_duration(url)
    return s


def _video_page_url(vid: str) -> str:
    return f"https://music.youtube.com/watch?v={vid}"


def _cached_stream(vid: str) -> tuple[Optional[str], int]:
    now = time.time()
    with _stream_lock:
        item = _stream_cache.get(str(vid or ""))
        if not item:
            return None, 0
        if now - float(item.get("ts", 0) or 0) > _STREAM_TTL_SECONDS:
            _stream_cache.pop(str(vid or ""), None)
            return None, 0
        return item.get("url"), int(item.get("duration", 0) or 0)


def _invalidate_cached_stream(vid: str) -> None:
    with _stream_lock:
        _stream_cache.pop(str(vid or ""), None)


def _wait_cached_stream(vid: str, timeout: float = 2.0) -> tuple[Optional[str], int]:
    deadline = time.monotonic() + max(0.0, float(timeout or 0))
    while time.monotonic() < deadline:
        cached_url, cached_dur = _cached_stream(vid)
        if cached_url:
            return cached_url, cached_dur
        with _stream_lock:
            loading = vid in _stream_loading
        if not loading:
            return None, 0
        time.sleep(0.05)
    return _cached_stream(vid)


def _resolve_stream_for_video(vid: str) -> tuple[Optional[str], int]:
    vid = str(vid or "").strip()
    if not vid:
        return None, 0
    cached_url, cached_dur = _cached_stream(vid)
    if cached_url:
        return cached_url, cached_dur
    stream, duration = _get_stream_url_and_duration(_video_page_url(vid))
    if stream:
        with _stream_lock:
            _stream_cache[vid] = {"url": stream, "duration": int(duration or 0), "ts": time.time()}
    return stream, int(duration or 0)


def _prefetch_video(vid: str):
    vid = str(vid or "").strip()
    if not vid:
        return
    cached_url, _ = _cached_stream(vid)
    if cached_url:
        return
    with _stream_lock:
        if vid in _stream_loading:
            return
        _stream_loading.add(vid)

    def worker():
        try:
            _resolve_stream_for_video(vid)
        finally:
            with _stream_lock:
                _stream_loading.discard(vid)

    threading.Thread(target=worker, daemon=True).start()


def warmup() -> bool:
    return _start_mpv()


def prefetch_tracks(tracks, start_index: int = 0, count: int = 4) -> dict:
    """Warm MPV and resolve at most the first candidate in the background."""
    try:
        start = max(0, int(start_index or 0))
    except Exception:
        start = 0
    try:
        n = max(0, min(8, int(count or 0)))
    except Exception:
        n = 0

    _start_mpv()
    if n <= 0:
        return {"scheduled": 0}
    for item in list(tracks or [])[start:start + n]:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("videoId") or item.get("video_id") or "").strip()
        if vid:
            _prefetch_video(vid)
            return {"scheduled": 1}
    return {"scheduled": 0}


def _prefetch_next_tracks(count: int = 3):
    if not _playlist:
        return
    items = []
    for offset in range(1, max(1, count) + 1):
        idx = (_playlist_idx + offset) % len(_playlist)
        items.append(_playlist[idx])
    prefetch_tracks(items, 0, len(items))


def _crossfade_trigger_margin() -> float:
    """Seconds before EOF to start preparing a crossfade transition."""
    return max(1.0, float(_crossfade_secs)) + _CROSSFADE_PREPARE_SECONDS


def _is_authoritative_playback_sample(slot: int) -> bool:
    """Whether a polled MPV slot may update the shared playback metadata."""
    return slot == _active_slot and not _crossfade_fading_out


def _reload_current_stream(vid: str, title: str, artists: str, bad_url: str = ""):
    """Reload the track using the YouTube Music URL (mpv yt-dlp hook re-resolves).
    bad_url is ignored (kept for call-site compatibility) — we no longer pass CDN URLs."""
    with _lock:
        if _last_meta.get("videoId") != vid:
            return  # user already switched to another track
    n = _reload_counts.get(vid, 0)
    if n >= _MAX_RELOADS_PER_TRACK:
        if vid not in _reload_givenup:
            _reload_givenup.add(vid)
            _log(
                f"'{title}': la reproducción no avanza tras {n} reintentos. "
                "Comprueba tu conexión a internet o intenta cambiar la calidad de audio "
                "en Ajustes (p.ej. de 'Máxima' a 'Opus ~160 kbps')."
            )
        return
    _reload_counts[vid] = n + 1
    _invalidate_cached_stream(vid)
    _log(f"'{title}': reintentando carga via yt-dlp hook (intento {n + 1})")
    yt_url = _video_page_url(vid)
    with _lock:
        if _last_meta.get("videoId") != vid:
            return
    response = _ipc_request(["loadfile", yt_url, "replace"])
    if not response or response.get("error") != "success":
        _log(f"'{title}': mpv rechazó la recarga")
        return
    with _lock:
        _last_meta.update({
            "position": 0,
            "playing": True,
            "ready": False,
            "buffering": False,
            "state": "loading",
            "_sampled_at": time.monotonic(),
            "_started": False,
        })


def _play_video(vid: str, title: str, artists: str) -> str:
    """Internal: start mpv playback by passing the YouTube Music URL directly.
    mpv's yt-dlp hook resolves the stream with the correct headers and format."""
    if _shutting_down:
        return "Aplicación cerrándose."
    vid = str(vid or "").strip()
    if not _start_mpv():
        return "mpv no pudo arrancarse."
    if _shutting_down:
        return "Aplicación cerrándose."
    # Consume a stream resolved while the user browsed. If it is still in
    # flight, wait briefly instead of launching a second yt-dlp subprocess via
    # the MPV hook. Expired/missing cache falls back to the page URL.
    cached_url, stream_duration = _cached_stream(vid)
    if not cached_url:
        cached_url, stream_duration = _wait_cached_stream(vid, timeout=3.5)
    source_url = cached_url or _video_page_url(vid)
    source_kind = "prefetched stream" if cached_url else "yt-dlp hook"
    _log(f"▶ loadfile '{title}' slot={_active_slot} dur={stream_duration}s ({source_kind})")
    response = _ipc_request(["loadfile", source_url, "replace"])
    ok = bool(response and response.get("error") == "success")
    if ok:
        _reset_reload_guard()   # fresh track → allow reload retries again
        # Honour the user's preferred volume (e.g. a non-100 default at startup)
        _send_command(["set_property", "volume", _user_volume])
        with _lock:
            _last_meta.update({
                "title": title,
                "artists": artists,
                "videoId": vid,
                "duration": float(stream_duration or 0),
                "position": 0,
                "playing": True,
                "ready": False,
                "buffering": False,
                "state": "loading",
                "_sampled_at": time.monotonic(),
                "_started": False,
            })
        _ensure_autoplay_worker()
        # Resolve the immediate successor while this track is playing.  The
        # crossfade can then spend its lead time opening the alternate MPV
        # output instead of waiting for yt-dlp at the end of the track.
        _prefetch_next_tracks()
        _verify_stream_started(vid, title, artists)
        return f"Reproduciendo '{title}' — {artists}."
    _log(f"ERROR: loadfile falló para '{title}'")
    return "No se pudo cargar la canción en mpv."


def _verify_stream_started(vid: str, title: str, artists: str):
    """Spawn a daemon thread that checks whether mpv actually started playing.
    First check at t=12s (yt-dlp hook resolution takes up to ~8s); if pos is
    near 0 but eof/idle not set (buffering stall), do a second check at t=20s."""
    def worker():
        time.sleep(12.0)
        with _lock:
            if _last_meta.get("videoId") != vid:
                return
            if not _last_meta.get("playing") and _last_meta.get("ready"):
                return
        snapshot = _read_mpv_playback_state()
        pos = snapshot.get("time-pos")
        eof = snapshot.get("eof-reached")
        idle = snapshot.get("idle-active")
        playing_ok = _derive_playback_phase(
            snapshot, was_ready=False, requested_playing=True
        )["progressing"]
        stream_failed = bool(eof) or bool(idle)
        _log(f"verify '{title}': pos={pos} eof={eof} idle={idle} → {'OK' if playing_ok else 'FALLO'}")
        if playing_ok:
            return
        if stream_failed:
            _reload_current_stream(vid, title, artists, "")
            return
        # pos near 0 but mpv hasn't reported eof/idle yet — wait more then reload
        time.sleep(8.0)
        with _lock:
            if _last_meta.get("videoId") != vid:
                return
        snapshot2 = _read_mpv_playback_state()
        pos2 = snapshot2.get("time-pos")
        eof2 = snapshot2.get("eof-reached")
        idle2 = snapshot2.get("idle-active")
        playing_ok2 = _derive_playback_phase(
            snapshot2, was_ready=False, requested_playing=True
        )["progressing"]
        _log(f"verify2 '{title}': pos={pos2} eof={eof2} idle={idle2} → {'OK' if playing_ok2 else 'FALLO'}")
        if not playing_ok2:
            _reload_current_stream(vid, title, artists, "")
    threading.Thread(target=worker, daemon=True).start()


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on", "si", "sí")


def _artists_text(artists) -> str:
    if not artists:
        return ""
    if isinstance(artists, str):
        return artists
    if isinstance(artists, list):
        names = []
        for a in artists:
            if isinstance(a, dict):
                nm = a.get("name") or a.get("artist") or ""
                if nm:
                    names.append(str(nm))
            elif a:
                names.append(str(a))
        return ", ".join(names)
    return str(artists)


def _begin_crossfade_overlap(
    next_vid: str, next_title: str, next_artists: str,
    next_url: str, next_dur: int = 0,
) -> bool:
    """Preload the incoming slot, then fade only after it is actively playing."""
    global _active_slot, _xfade_in_progress, _crossfade_fading_out
    global _autoplay_last_switch

    if _xfade_in_progress or _shutting_down:
        _log(f"XFADE: ignorado (xfade_in_progress={_xfade_in_progress})")
        return False

    alt_slot = 1 - _active_slot
    alt_pipe = _PIPE_PATHS[alt_slot]

    _log(f"XFADE: iniciando — slot activo={_active_slot} → slot nuevo={alt_slot}")

    # Start the alternate mpv slot
    if not _start_mpv(slot=alt_slot):
        _log(f"XFADE: ERROR — no se pudo arrancar mpv slot={alt_slot}, fallback a cambio directo")
        return False

    # Pause before load so resolution/opening does not consume the first seconds
    # of the incoming track at volume zero.
    next_yt_url = next_url or _video_page_url(next_vid)
    _send_command(["set_property", "volume", 0], pipe=alt_pipe)
    _send_command(["set_property", "pause", True], pipe=alt_pipe)
    response = _ipc_request(["loadfile", next_yt_url, "replace"], pipe=alt_pipe)
    if not response or response.get("error") != "success":
        _log(f"XFADE: loadfile rechazado en slot={alt_slot}")
        return False
    _log(f"XFADE: '{next_title}' precargando en slot={alt_slot} vol=0")

    cf_dur   = max(1.0, float(_crossfade_secs))
    cur_pipe = _cur_pipe()
    _xfade_in_progress = True
    _crossfade_fading_out = False
    _autoplay_last_switch = time.time()

    def _fade():
        global _active_slot, _xfade_in_progress, _crossfade_fading_out

        def _abort_and_fallback(reason: str):
            global _xfade_in_progress, _crossfade_fading_out
            _log(f"XFADE: abortado ({reason}); cambio directo")
            try:
                proc = _procs[alt_slot]
                if proc is not None and proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
            _procs[alt_slot] = None
            _send_command(["set_property", "volume", _user_volume], pipe=cur_pipe)
            _crossfade_fading_out = False
            _xfade_in_progress = False
            _invalidate_cached_stream(next_vid)
            if not _shutting_down:
                _play_video(next_vid, next_title, next_artists)

        if not _wait_for_audio_output_ready(alt_pipe, timeout=12.0):
            _abort_and_fallback("la salida entrante no abrió")
            return
        _send_command(["set_property", "pause", False], pipe=alt_pipe)
        if not _wait_for_audio_ready(alt_pipe, timeout=4.0):
            _abort_and_fallback("la pista entrante no inició")
            return

        incoming = _read_mpv_playback_state(pipe=alt_pipe)
        outgoing = _read_mpv_playback_state(pipe=cur_pipe)
        incoming_pos = float(incoming.get("time-pos") or 0.0)
        incoming_dur = float(incoming.get("duration") or next_dur or 0.0)
        _reset_reload_guard()
        _crossfade_fading_out = True
        with _lock:
            _last_meta.update({
                "title": next_title,
                "artists": next_artists,
                "videoId": next_vid,
                "duration": incoming_dur,
                "position": incoming_pos,
                "playing": True,
                "ready": True,
                "buffering": False,
                "state": "playing",
                "_sampled_at": time.monotonic(),
                "_started": True,
            })

        outgoing_pos = float(outgoing.get("time-pos") or 0.0)
        outgoing_dur = float(outgoing.get("duration") or 0.0)
        remaining = max(0.0, outgoing_dur - outgoing_pos) if outgoing_dur > 0 else cf_dur
        fade_dur = min(cf_dur, remaining) if remaining > 0 else 0.0
        steps = max(1, int(max(0.05, fade_dur) / 0.05))
        for i in range(steps + 1):
            if _shutting_down:
                break
            progress = i / steps          # 0.0 → 1.0
            vol_out  = int(_user_volume * (1.0 - progress))
            vol_in   = int(_user_volume * progress)
            _send_command(["set_property", "volume", vol_out], pipe=cur_pipe)
            _send_command(["set_property", "volume", vol_in],  pipe=alt_pipe)
            if fade_dur > 0:
                time.sleep(fade_dur / steps)

        if _shutting_down:
            _xfade_in_progress = False
            return

        # Overlap complete — promote alt slot to active
        old_slot = _active_slot
        old_proc = _procs[old_slot]
        _active_slot = alt_slot          # all future IPC goes to the new pipe
        _log(f"XFADE: completado — slot activo ahora={_active_slot}, matando slot={old_slot}")

        # Kill the outgoing process (it is silent now)
        try:
            if old_proc is not None and old_proc.poll() is None:
                old_proc.terminate()
        except Exception:
            pass
        _procs[old_slot] = None

        # Guarantee full volume on new primary
        _send_command(["set_property", "volume", _user_volume])

        final_state = _read_mpv_playback_state()
        final_pos = float(final_state.get("time-pos") or incoming_pos)
        final_dur = float(final_state.get("duration") or incoming_dur)
        with _lock:
            _last_meta.update({
                "title": next_title,
                "artists": next_artists,
                "videoId": next_vid,
                "duration": final_dur,
                "position": final_pos,
                "playing": True,
                "ready": True,
                "buffering": False,
                "state": "playing",
                "_sampled_at": time.monotonic(),
                "_started": True,
            })

        _crossfade_fading_out = False
        _xfade_in_progress = False

    threading.Thread(target=_fade, daemon=True).start()
    return True


def _ensure_autoplay_worker() -> None:
    """Start the combined position-poller + autoplay thread if not running."""
    global _autoplay_thread
    if _autoplay_thread is not None and _autoplay_thread.is_alive():
        return

    def _worker():
        global _autoplay_last_switch, _crossfade_fading_out
        _poll_pos  = 0.0
        _poll_dur  = 0.0
        _eof_seen  = False
        _last_vid  = ""
        _tick      = 0
        _stall_pos     = None   # last pos snapshot for stall detection
        _stall_since   = None   # monotonic time when pos last changed
        _ao_failure_since = None

        while not _shutting_down:
            try:
                polled_slot = _active_slot
                proc = _procs[polled_slot]
                if proc is not None and proc.poll() is None:
                    # --- Poll live position/state from mpv IPC ---
                    snapshot = _read_mpv_playback_state()
                    # A crossfade can promote the alternate slot while this IPC
                    # read is in flight.  Never publish the old slot's final
                    # position/duration over the new track's metadata: that made
                    # the seek bar jump to the end and back for one polling tick.
                    if not _is_authoritative_playback_sample(polled_slot):
                        time.sleep(0.05)
                        continue
                    pos = snapshot.get("time-pos")
                    dur = snapshot.get("duration")
                    paused = snapshot.get("pause")
                    eof = snapshot.get("eof-reached")
                    idle = snapshot.get("idle-active")

                    with _lock:
                        current_vid = _last_meta.get("videoId", "")
                        # While the alternate slot is only preloading, the active
                        # slot remains authoritative. Once the volume ramps begin,
                        # metadata has switched to the incoming track.
                    if not _crossfade_fading_out:
                        phase = _apply_mpv_playback_state(snapshot)
                    else:
                        phase = _derive_playback_phase(
                            snapshot,
                            was_ready=True,
                            requested_playing=not bool(paused),
                        )

                    # When the track changes, reset all local polling state
                    if current_vid != _last_vid:
                        _log(f"worker: nueva pista detectada '{current_vid[:8]}' (era '{_last_vid[:8]}')")
                        _last_vid = current_vid
                        _poll_pos = 0.0
                        _poll_dur = 0.0
                        _eof_seen = False

                    if pos is not None:
                        _poll_pos = float(pos)
                        if not bool(eof):
                            _eof_seen = False
                    if dur is not None:
                        _poll_dur = float(dur)

                    # MPV can keep an explicit WASAPI GUID after Bluetooth has
                    # disconnected. Reconcile its live device list periodically;
                    # if the file initialized but AO did not, fall back to auto
                    # and reload once because setting audio-device alone cannot
                    # re-enable an already disabled audio chain.
                    if _tick % 5 == 0:
                        device_list = _get_mpv_property("audio-device-list")
                        if isinstance(device_list, list):
                            changed = _reconcile_audio_output_device_list(device_list)
                            if changed and current_vid:
                                _audio_reset_tried.add(current_vid)
                                with _lock:
                                    recover_title = _last_meta.get("title", "")
                                    recover_artists = _last_meta.get("artists", "")
                                threading.Thread(
                                    target=_reload_current_stream,
                                    args=(current_vid, recover_title, recover_artists, ""),
                                    daemon=True,
                                ).start()

                    ao_failed = (
                        pos is not None
                        and not _mpv_audio_ready(snapshot)
                        and not bool(snapshot.get("seeking"))
                        and not bool(eof)
                        and not bool(idle)
                    )
                    if ao_failed and _audio_device and current_vid:
                        now_ao = time.monotonic()
                        if _ao_failure_since is None:
                            _ao_failure_since = now_ao
                        elif (
                            now_ao - _ao_failure_since >= _AUDIO_OPEN_GRACE_SECONDS
                            and current_vid not in _audio_reset_tried
                        ):
                            _audio_reset_tried.add(current_vid)
                            _ao_failure_since = None
                            with _lock:
                                recover_title = _last_meta.get("title", "")
                                recover_artists = _last_meta.get("artists", "")
                            set_audio_output_device("")
                            threading.Thread(
                                target=_reload_current_stream,
                                args=(current_vid, recover_title, recover_artists, ""),
                                daemon=True,
                            ).start()
                    else:
                        _ao_failure_since = None

                    # --- Stall detection: pos frozen for 10s while not paused/eof ---
                    if (phase["progressing"] and pos is not None and not bool(eof)
                            and not _xfade_in_progress and current_vid):
                        now_m = time.monotonic()
                        if _stall_pos is None or abs(float(pos) - _stall_pos) > 0.1:
                            _stall_pos = float(pos)
                            _stall_since = now_m
                        elif now_m - _stall_since > 10.0:
                            _log(f"STALL: pos={pos} congelada 10s — recargando via yt-dlp hook")
                            _stall_pos = None
                            _stall_since = None
                            _stall_vid = current_vid
                            with _lock:
                                _stall_title   = _last_meta.get("title", "")
                                _stall_artists = _last_meta.get("artists", "")
                            threading.Thread(
                                target=_reload_current_stream,
                                args=(_stall_vid, _stall_title, _stall_artists, ""),
                                daemon=True,
                            ).start()
                    else:
                        _stall_pos   = None
                        _stall_since = None

                    # Periodic status log every ~5 ticks (~4s)
                    _tick += 1
                    if _tick % 5 == 0:
                        title_short = (current_vid[:8] if current_vid else "—")
                        _log(f"  pos={_poll_pos:.1f}/{_poll_dur:.1f}s eof={bool(eof)} "
                             f"idle={bool(idle)} paused={bool(paused)} "
                             f"eof_seen={_eof_seen} xfade={_xfade_in_progress} slot={_active_slot} [{title_short}]")

                    # --- Autoplay: advance on eof-reached OR near end ---
                    if _autoplay_enabled and _playlist and not _xfade_in_progress:
                        cf_margin = _crossfade_trigger_margin() if _crossfade_enabled else 1.5
                        eof_hit  = bool(eof)
                        near_end = _poll_dur > 0 and _poll_pos >= _poll_dur - cf_margin
                        if (eof_hit or near_end) and not _eof_seen:
                            now = time.time()
                            cooldown_ok = now - _autoplay_last_switch > 6.0
                            _log(f"autoplay trigger: eof={eof_hit} near_end={near_end} "
                                 f"pos={_poll_pos:.1f} dur={_poll_dur:.1f} cooldown_ok={cooldown_ok}")
                            if cooldown_ok:
                                if eof_hit and _poll_pos < 1.0 and not near_end:
                                    _log("  → EOF en pos~0 (stream inválido), silenciando hasta retry")
                                    _eof_seen = True
                                else:
                                    _log(f"  → avanzando a siguiente pista")
                                    _eof_seen = True
                                    _autoplay_last_switch = now
                                    _crossfade_fading_out = False
                                    next()
                else:
                    with _lock:
                        _last_meta.update({
                            "playing": False,
                            "ready": False,
                            "buffering": False,
                            "state": "stopped",
                            "_started": False,
                        })
            except Exception as exc:
                _log(f"worker: error sondeando mpv: {exc}")
            time.sleep(0.8)

    _autoplay_thread = threading.Thread(target=_worker, daemon=True)
    _autoplay_thread.start()


def set_autoplay(enabled: bool = True) -> str:
    global _autoplay_enabled
    _autoplay_enabled = _to_bool(enabled)
    if _autoplay_enabled:
        _ensure_autoplay_worker()
    return f"Autoplay {'activado' if _autoplay_enabled else 'desactivado'}."


def show_queue(limit: int = 20) -> str:
    if not _playlist:
        return "La cola está vacía."
    try:
        lim = max(1, min(100, int(limit)))
    except Exception:
        lim = 20
    lines = [f"Cola ({len(_playlist)} canciones):"]
    end = min(len(_playlist), lim)
    for i in range(end):
        t = _playlist[i]
        mark = "▶" if i == _playlist_idx else " "
        lines.append(f"{mark} {i+1}. {t.get('title','')} — {t.get('artists','')}")
    if len(_playlist) > lim:
        lines.append(f"... y {len(_playlist)-lim} más")
    return "\n".join(lines)


def list_playlists(limit: int | None = None) -> str:
    try:
        from actions.ytmusic import _get_ytmusic
        yt = _get_ytmusic(require_auth=True)
        try:
            resolved_limit = None if limit is None or int(limit) <= 0 else int(limit)
        except (TypeError, ValueError):
            resolved_limit = None
        pls = yt.get_library_playlists(limit=resolved_limit)
    except Exception as e:
        return f"No se pudieron leer tus listas: {e}"

    if not pls:
        return "No se encontraron listas en tu biblioteca."
    lines = [f"Tus listas ({len(pls)}):"]
    for p in pls:
        title = p.get("title", "")
        author = p.get("author", "")
        pid = p.get("playlistId") or p.get("browseId") or ""
        lines.append(f"- {title} — {author} [{pid}]")
    return "\n".join(lines)


def _build_playlist_from_tracks(tracks, shuffle: bool = False) -> list:
    out = []
    for t in tracks or []:
        vid = t.get("videoId")
        if not vid:
            continue
        out.append({
            "videoId": vid,
            "title": t.get("title", ""),
            "artists": _artists_text(t.get("artists")),
        })
    if shuffle and out:
        random.shuffle(out)
    return out


def _load_and_play_playlist(items: list, start_idx: int = 0) -> str:
    global _playlist, _playlist_idx
    if not items:
        return "La lista está vacía."
    _playlist = items
    _playlist_idx = max(0, min(int(start_idx), len(_playlist) - 1))
    cur = _playlist[_playlist_idx]
    return _play_video(cur["videoId"], cur["title"], cur["artists"])


def add_to_queue(video_id: str = "", title: str = "", artists: str = "") -> str:
    """Append a track to the end of the queue without interrupting playback.

    Starts playback immediately if nothing is queued/playing yet (there's
    nothing to append after).
    """
    global _playlist
    vid = str(video_id or "").strip()
    if not vid:
        return "No hay videoId para encolar."
    item = {"videoId": vid, "title": str(title or ""), "artists": str(artists or "")}
    if not _playlist:
        return _load_and_play_playlist([item], 0)
    _playlist.append(item)
    return f"Añadido a la cola: {item['title'] or vid} (posición {len(_playlist)})"


def play_track(video_id: str = "", title: str = "", artists: str = "") -> str:
    vid = str(video_id or "").strip()
    if not vid:
        return "No hay videoId para reproducir."
    return _load_and_play_playlist([{
        "videoId": vid,
        "title": str(title or ""),
        "artists": str(artists or ""),
    }], 0)


def play_tracks(tracks, start_index: int = 0, shuffle: bool = False) -> str:
    items = _build_playlist_from_tracks(tracks or [], shuffle=shuffle)
    if not items:
        return "La lista está vacía."
    return _load_and_play_playlist(items, start_index)


def play_liked(limit: int | None = None, shuffle: bool = False) -> str:
    try:
        from actions.ytmusic import get_liked_songs
        songs = get_liked_songs(limit=limit)
    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"No se pudieron cargar tus Me gusta: {e}"

    items = _build_playlist_from_tracks(songs, shuffle=shuffle)
    if not items:
        return "No tienes canciones en Me gusta."
    return _load_and_play_playlist(items, 0)


def play_playlist(query_or_id: str = "", limit: int | None = None, shuffle: bool = False, start_index: int = 0) -> str:
    try:
        from actions.ytmusic import list_playlist_tracks
        tracks = list_playlist_tracks(
            query_or_id=query_or_id,
            limit=limit,
            shuffle=False,
        )
        if not tracks:
            return "No encontré esa lista. Usa list_playlists para ver tus listas."

        items = _build_playlist_from_tracks(tracks, shuffle=shuffle)
        if not items:
            return "Esa lista no tiene pistas reproducibles."
        return _load_and_play_playlist(items, start_index)
    except Exception as e:
        return f"No se pudo reproducir la lista: {e}"


def play(query: str) -> str:
    """Search using actions.ytmusic.search_songs and play first match headless.
    Loads up to 10 results into the playlist for next()/previous() navigation."""
    global _playlist, _playlist_idx
    try:
        from actions.ytmusic import search_songs
    except Exception:
        return "No se puede buscar; módulo `actions.ytmusic` no disponible."

    results = search_songs(query, limit=10)
    if not results:
        return f"No se encontró '{query}'."

    # Build playlist
    new_pl = []
    for r in results:
        vid = r.get("videoId")
        if vid:
            new_pl.append({
                "videoId": vid,
                "title":   r.get("title", ""),
                "artists": r.get("artists", ""),
            })
    if not new_pl:
        return f"No se pudo obtener URL para '{query}'."

    return _load_and_play_playlist(new_pl, 0)


def _crossfade_transition(t: dict, allow_crossfade: bool = True) -> str:
    """Attempt a true crossfade overlap to track t.
    Falls back to instant switch if the alt slot can't start in time, or if
    crossfade is not allowed for this transition (e.g. a manual skip while the
    'crossfade on skip' option is off)."""
    if allow_crossfade and _crossfade_enabled and not _xfade_in_progress:
        cached_url, dur = _cached_stream(t["videoId"])
        if not dur:
            _, dur = _wait_cached_stream(t["videoId"], timeout=0.5)
        if _begin_crossfade_overlap(
            t["videoId"], t["title"], t["artists"], cached_url or "", dur
        ):
            return f"Crossfade → '{t['title']}'."
    # Fallback: instant switch at full volume
    _log(f"cambio directo → '{t['title']}'")
    _send_command(["set_property", "volume", _user_volume])
    return _play_video(t["videoId"], t["title"], t["artists"])


def next(manual: bool = False) -> str:
    """Skip to next track in playlist.

    manual=True marks a user-initiated skip (next button / voice command); such
    skips only crossfade when the user enabled 'crossfade on skip'. Auto-advance
    from the playback worker passes manual=False and always crossfades if enabled.
    """
    global _playlist_idx, _crossfade_fading_out
    if not _playlist:
        return "No hay lista de reproducción."
    _playlist_idx = (_playlist_idx + 1) % len(_playlist)
    _crossfade_fading_out = False
    allow = (not manual) or _crossfade_on_skip
    return _crossfade_transition(_playlist[_playlist_idx], allow_crossfade=allow)


def previous(manual: bool = False) -> str:
    """Skip to previous track in playlist. See next() for the manual flag."""
    global _playlist_idx, _crossfade_fading_out
    if not _playlist:
        return "No hay lista de reproducción."
    _playlist_idx = (_playlist_idx - 1) % len(_playlist)
    _crossfade_fading_out = False
    allow = (not manual) or _crossfade_on_skip
    return _crossfade_transition(_playlist[_playlist_idx], allow_crossfade=allow)


def pause() -> bool:
    ok = _send_command(["set_property", "pause", True])
    if ok:
        with _lock:
            _last_meta["playing"] = False
            _last_meta["buffering"] = False
            _last_meta["state"] = "paused" if _last_meta.get("ready") else "stopped"
            _last_meta["_sampled_at"] = time.monotonic()
    return ok


def resume() -> bool:
    ok = _send_command(["set_property", "pause", False])
    if ok:
        with _lock:
            _last_meta["playing"] = True
            _last_meta["state"] = "playing" if _last_meta.get("ready") else "loading"
            _last_meta["_sampled_at"] = time.monotonic()
    return ok


def toggle_play() -> bool:
    return _send_command(["cycle", "pause"]) 


def stop() -> bool:
    ok = _send_command(["stop"])
    if ok:
        with _lock:
            _last_meta.update({
                "title": "",
                "artists": "",
                "videoId": "",
                "duration": 0,
                "position": 0,
                "playing": False,
                "ready": False,
                "buffering": False,
                "state": "stopped",
                "_sampled_at": 0.0,
                "_started": False,
            })
    return ok


def volume(level: int) -> bool:
    global _user_volume
    try:
        lvl = max(0, min(100, int(level)))
    except Exception:
        lvl = 50
    _user_volume = lvl
    ok = _send_command(["set_property", "volume", lvl])
    return ok


def set_crossfade(seconds: int = 3, enabled: bool = True) -> str:
    """Enable or disable crossfade and set its duration in seconds (1-15)."""
    global _crossfade_secs, _crossfade_enabled
    try:
        secs = max(1, min(15, int(seconds)))
    except Exception:
        secs = 3
    _crossfade_secs = secs
    _crossfade_enabled = bool(enabled)
    state = "activado" if _crossfade_enabled else "desactivado"
    return f"Crossfade {state} ({_crossfade_secs}s)."


def set_crossfade_on_skip(enabled: bool = False) -> str:
    """Choose whether the crossfade also applies to manual next/previous skips."""
    global _crossfade_on_skip
    _crossfade_on_skip = bool(enabled)
    return f"Crossfade al cambiar de canción {'activado' if _crossfade_on_skip else 'desactivado'}."


def get_crossfade() -> dict:
    """Return current crossfade settings."""
    return {
        "enabled": _crossfade_enabled,
        "seconds": _crossfade_secs,
        "on_skip": _crossfade_on_skip,
    }


def seek(seconds: int) -> bool:
    try:
        s = int(seconds)
    except Exception:
        return False
    ok = _send_command(["seek", s, "absolute"])
    if ok:
        with _lock:
            _last_meta["position"] = float(s)
            _last_meta["buffering"] = bool(_last_meta.get("ready"))
            _last_meta["state"] = "buffering" if _last_meta.get("ready") else "loading"
            _last_meta["_sampled_at"] = time.monotonic()
    return ok


def play_from_file(file_path: str, shuffle: bool = False) -> str:
    """Load a Jarvis playlist JSON exported with export_liked_to_file /
    export_playlist_to_file and start playback."""
    try:
        from actions.ytmusic import import_playlist_from_file
        tracks = import_playlist_from_file(file_path)
    except Exception as e:
        return f"No se pudo leer la playlist: {e}"
    if not tracks:
        return "La playlist importada está vacía o no tiene videoIds."
    return play_tracks(tracks, start_index=0, shuffle=shuffle)


def current() -> dict:
    with _lock:
        base = dict(_last_meta)
    active_proc = _procs[_active_slot]
    if active_proc is None or active_proc.poll() is not None:
        base["playing"] = False
        base["ready"] = False
        base["buffering"] = False
        base["state"] = "stopped"
        base.pop("_sampled_at", None)
        base.pop("_started", None)
        return base
    # If the poller thread is alive it already keeps _last_meta fresh every ~0.8s
    if _autoplay_thread is not None and _autoplay_thread.is_alive():
        sampled_at = float(base.pop("_sampled_at", 0.0) or 0.0)
        started = bool(base.pop("_started", False))
        ready = bool(base.get("ready", started))
        buffering = bool(base.get("buffering", False))
        state = str(base.get("state") or "")
        if not state:
            if base.get("playing") and not ready:
                state = "loading"
            elif base.get("playing") and buffering:
                state = "buffering"
            elif base.get("playing"):
                state = "playing"
            elif ready:
                state = "paused"
            else:
                state = "stopped"
        base["ready"] = ready
        base["buffering"] = buffering
        base["state"] = state
        # Until mpv has reported a real time-pos for this track, keep position
        # frozen instead of interpolating from wall-clock elapsed — otherwise
        # the counter climbs during the yt-dlp resolve/buffer window and then
        # snaps back down once the real (near-zero) position arrives.
        if ready and not buffering and state == "playing" and sampled_at > 0:
            elapsed = min(
                _MAX_PROGRESS_EXTRAPOLATION,
                max(0.0, time.monotonic() - sampled_at),
            )
            duration = float(base.get("duration") or 0.0)
            position = float(base.get("position") or 0.0) + elapsed
            base["position"] = min(duration, position) if duration > 0 else position
        return base
    # Poller not running yet: do a one-off live IPC query
    if active_proc is not None and active_proc.poll() is None:
        snapshot = _read_mpv_playback_state()
        _apply_mpv_playback_state(snapshot)
        with _lock:
            base = dict(_last_meta)
    base.pop("_sampled_at", None)
    base.pop("_started", None)
    ready = bool(base.get("ready", False))
    buffering = bool(base.get("buffering", False))
    if base.get("playing") and not ready:
        state = "loading"
    elif base.get("playing") and buffering:
        state = "buffering"
    elif base.get("playing"):
        state = "playing"
    elif ready:
        state = "paused"
    else:
        state = "stopped"
    base.update({"ready": ready, "buffering": buffering, "state": state})
    return base
