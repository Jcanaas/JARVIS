"""Qt-free `ui` adapter for the new pywebview desktop UI.

main.py's boot flow only ever talks to the `ui` object it constructs through
a duck-typed contract (see JarvisUI in ui/__init__.py) — JarvisLive (the
voice/agent loop) and PlaybackController (music) don't know or care whether
they're driving Qt widgets or a web frontend. WebUIHost implements the same
contract but sinks everything into actions/webui_server.py's SSE broadcaster
instead of Qt signals, so the existing agent/music engines keep working
unmodified in the new default boot mode.

Panels genuinely out of scope for the web UI MVP (camera, screen translate,
movies) get harmless no-op stubs rather than missing methods, since
JarvisLive calls them unconditionally as part of its normal flow.
"""
from __future__ import annotations

import threading
import time

from actions.paths import config_path, resource

API_FILE = config_path("api_keys.json")

# Holds the process's one QApplication (see _boot_headless_qt). PyQt6 makes
# Python own a parentless QApplication, so a local reference that goes out of
# scope gets garbage-collected — which destroys the singleton right along
# with it. A module-level reference is what keeps it alive for the process.
_qt_app = None


def _window_icon_path() -> str | None:
    """Path pywebview should use for the window/taskbar icon, or None if the
    bundled icon is missing. winforms' System.Drawing.Icon only loads .ico;
    GTK's set_icon_from_file wants a raster format, so each platform gets
    its own file extracted from the same source icon (see installer/)."""
    import sys

    name = "jarvis-icon.ico" if sys.platform == "win32" else "jarvis-icon-256.png"
    path = resource("installer", name)
    return str(path) if path.is_file() else None


def _boot_headless_qt() -> None:
    """Create the process's one QApplication, here, on the main thread,
    before webview.start() takes it over.

    This exists for actions/emulator_session.py: the embedded-emulators
    core needs a QOffscreenSurface/QOpenGLContext (see actions/libretro.py),
    which needs a live QApplication event loop in the process. Creating the
    QApplication on a background thread instead — the seemingly obvious
    place, since this thread is about to be handed to GTK — emulates fine
    but segfaults the whole process at interpreter shutdown, reproduced
    across every QPA platform plugin in testing; it's Qt's own
    "QCoreApplication belongs to the main thread" rule, not fixable from
    the emulator side. So it has to happen here, exactly once, and nothing
    below ever calls exec() on it — webview.start() below is the process's
    only real event loop.

    Best-effort: the emulators panel is optional, so a missing/broken
    PyQt6 install degrades to "no emulators", not a failed boot.
    """
    global _qt_app
    try:
        from PyQt6.QtWidgets import QApplication
        import sys

        _qt_app = QApplication.instance() or QApplication(sys.argv[:1])
    except Exception as exc:
        print(f"[WebUIHost] Emuladores no disponibles (PyQt6): {exc}")


class _WebRoot:
    def __init__(self, host: "WebUIHost"):
        self._host = host
        self._quit_callbacks: list = []
        self._window = None

    def protocol(self, *_):
        pass

    def mainloop(self):
        import sys

        if sys.platform not in ("win32", "darwin"):
            # WebKitGTK's DMA-BUF renderer needs a working Mesa GBM backend.
            # Some Wayland/hybrid-GPU setups deliberately export
            # GBM_BACKEND="" at the session level to dodge tearing (see e.g.
            # a Hyprland env.d script), which breaks GBM for every GTK app,
            # not just the compositor — WebKit then negotiates hardware
            # acceleration in a half-broken state instead of cleanly falling
            # back, which is what produces "MESA-LOADER: failed to open :
            # .../_gbm.so" and an unstable/freezing webview. Forcing the
            # older cairo/software compositing path sidesteps GBM entirely,
            # trading a bit of GPU accel for a renderer that doesn't hang.
            # setdefault so an explicit user override still wins.
            import os
            os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

        from actions import webui_server
        import webview

        from actions import app_settings

        url = self._host.dev_url or webui_server.start(self._host, port=self._host.port)
        window = webview.create_window(
            "J.A.R.V.I.S — MARK XXXIX",
            url=url,
            width=1180,
            height=760,
            min_size=(900, 620),
            on_top=bool(app_settings.get("ui_always_on_top", False)),
        )
        self._window = window

        def _on_closed():
            for cb in self._quit_callbacks:
                try:
                    cb()
                except Exception:
                    pass

        window.events.closed += _on_closed
        _boot_headless_qt()
        webview.start(icon=_window_icon_path())


class WebUIHost:
    def __init__(self, port: int | None = None, dev_url: str | None = None):
        self.port = port
        self.dev_url = dev_url
        self.root = _WebRoot(self)

        # Mirrors ui/__init__.py's _load_mic_state_from_config: "on"/"off"
        # force a fixed boot state, "remember" restores mic_last_state (the
        # value last saved by the muted setter below).
        from actions import app_settings
        mic_default = app_settings.get("mic_default_state", "on")
        if mic_default == "remember":
            self._muted = app_settings.get("mic_last_state", "on") == "off"
        else:
            self._muted = mic_default == "off"

        self._state: dict = {
            "playback": {},
            "music_playing": False,
            "whatsapp_unread": 0,
            "mic_available": True,
            "state": "MUTED" if self._muted else "LISTENING",
        }

        self.playback_controller = None
        self.whatsapp_manager = None
        self.on_text_command = None
        self.on_playback_command = None
        self.on_download_cancel = None
        self.interrupt_callback = None

        try:
            from actions.event_bus import subscribe, ActionEvent
            subscribe(ActionEvent.LOG, lambda data: self.write_log(data.get("message", "")))
            subscribe(ActionEvent.TOAST, lambda data: self.show_toast(data.get("text", ""), data.get("duration", 2500)))
            subscribe(ActionEvent.ERROR, lambda data: self.write_log(f"[{data.get('source', 'Error')}] {data.get('message', '')}"))
        except Exception:
            pass

        # Persistent torrent downloads (games/movies/anime) — same manager
        # and resume-on-launch behavior as ui/__init__.py:269-276, just
        # broadcast over SSE instead of a Qt signal.
        try:
            import threading as _threading
            from actions.download_manager import get_manager as _get_dl_manager
            self._dl_manager = _get_dl_manager()
            self._dl_manager.add_listener(
                lambda snapshot: self._publish("downloads", [d.to_dict() for d in snapshot])
            )
            _threading.Timer(0, self._dl_manager.resume_interrupted).start()
        except Exception:
            self._dl_manager = None

    def _publish(self, event_type: str, data: dict):
        try:
            from actions import webui_server
            webui_server.publish(event_type, data)
        except Exception:
            pass

    def get_state(self) -> dict:
        return dict(self._state)

    # -- quit / lifecycle -------------------------------------------------
    def on_quit(self, callback):
        self.root._quit_callbacks.append(callback)

    def toggle_fullscreen(self):
        window = self.root._window
        if window is not None:
            window.toggle_fullscreen()

    def set_always_on_top(self, on_top: bool):
        window = self.root._window
        if window is not None:
            window.on_top = bool(on_top)

    # -- logging / toasts ---------------------------------------------------
    def write_log(self, text: str):
        self._publish("log", {"text": str(text)})

    def show_toast(self, text: str, ms: int = 2500):
        self._publish("toast", {"text": str(text), "duration": int(ms)})

    def show_whatsapp_notification(self, entry: dict):
        # This single event also drives WhatsAppPanel's in-app live refresh
        # (a separate concern from the floating toast in legacy — there it's
        # on_new_message vs. _show_wa_notification/_wa_notify_sig) so it must
        # always publish. The "notificaciones flotantes" toggle/duration only
        # gates whether a *toast popup* renders, so that check travels in the
        # payload for the toast component to honour, same values
        # ui/__init__.py's _show_wa_notification reads.
        from actions import app_settings
        try:
            dur_s = int(app_settings.get("whatsapp_notification_duration_s", 7))
        except Exception:
            dur_s = 7
        payload = dict(entry or {})
        payload["toast_enabled"] = bool(app_settings.get("whatsapp_notifications", True))
        payload["duration_ms"] = dur_s * 1000 if dur_s > 0 else 0
        self._publish("whatsapp_notification", payload)

    def open_whatsapp_chat(self, contact: str = ""):
        self._publish("open_chat", {"contact": str(contact or "")})

    def set_whatsapp_unread_count(self, count: int):
        self._state["whatsapp_unread"] = int(count)
        self._publish("whatsapp_unread", {"count": int(count)})

    # -- voice / mic state --------------------------------------------------
    def set_state(self, state: str):
        self._state["state"] = state
        self._publish("state", {"state": state})

    def set_mic_available(self, available: bool):
        available = bool(available)
        was_available = self._state.get("mic_available", True)
        self._state["mic_available"] = available
        self._publish("mic", {"available": available})
        if available == was_available:
            return
        # Mirrors ui/__init__.py's _apply_mic_available: force-mute (and
        # remember what to restore) when the mic disappears, then restore on
        # reconnect per mic_default_state — same "on"/"off"/"remember"
        # semantics as the initial-boot load above. Writes self._muted
        # directly rather than through the `muted` setter: that setter
        # always persists the *new* value in remember mode, which here
        # would overwrite the real last user state with the forced "off".
        from actions import app_settings
        if not available:
            if app_settings.get("mic_default_state", "on") == "remember":
                app_settings.set("mic_last_state", "off" if self._muted else "on")
            self._muted = True
            self.set_state("MUTED")
        else:
            mic_default = app_settings.get("mic_default_state", "on")
            if mic_default == "remember":
                self._muted = app_settings.get("mic_last_state", "on") == "off"
            else:
                self._muted = mic_default == "off"
            self.set_state("MUTED" if self._muted else "LISTENING")

    def start_speaking(self):
        self.set_state("SPEAKING")
        self.set_audio_level(0.6)

    def stop_speaking(self):
        self.set_audio_level(0.0)
        if not self.muted:
            self.set_state("LISTENING")

    def set_audio_level(self, level: float):
        self._publish("audio", {"level": float(level)})

    def set_audio_bands(self, bass: float, mid: float, treble: float):
        self._publish("audio_bands", {"bass": float(bass), "mid": float(mid), "treble": float(treble)})

    def set_fft_bins(self, bins):
        self._publish("fft", {"bins": list(bins)})

    # -- downloads / tasks ----------------------------------------------------
    def set_download_state(self, state: dict):
        self._publish("task", dict(state or {}))

    def set_task_state(self, state: dict):
        self.set_download_state(state)

    def request_download_cancel(self):
        cb = self.on_download_cancel
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    # -- music / playback -----------------------------------------------------
    def set_music_playing(self, playing: bool):
        # Distinct from update_playback's "playing": this is the visualizer
        # gate (audio actually progressing AND the visualizer enabled), what
        # ui/widgets/hud.py drives its animation from. Keeping it out of the
        # playback dict stops the poller and this from overwriting each other.
        if bool(playing) == self._state.get("music_playing"):
            return
        self._state["music_playing"] = bool(playing)
        self._publish("music_playing", {"playing": bool(playing)})

    def set_music_volume(self, level: int):
        self._state["playback"]["volume"] = int(level)
        self._publish("playback_volume", {"volume": int(level)})

    def update_playback(
        self,
        title: str,
        artists: str,
        position: float,
        duration: float,
        playing: bool,
        video_id: str = "",
        liked: bool | None = None,
        ready: bool | None = None,
        buffering: bool = False,
        state: str = "",
        thumbnail: str = "",
    ):
        snapshot = {
            "title": title,
            "artists": artists,
            "position": round(float(position or 0), 1),
            "duration": duration,
            "playing": playing,
            "videoId": video_id,
            "liked": liked,
            "ready": ready,
            "buffering": buffering,
            "state": state,
            "thumbnail": thumbnail,
        }
        previous = self._state.get("playback") or {}
        # Volume is owned by set_music_volume, not by the poller — carry it
        # across or this wholesale replacement drops it every tick and the
        # volume slider snaps back to its default.
        if "volume" in previous:
            snapshot["volume"] = previous["volume"]
        # main.py's playback poller calls this on a tight loop even when
        # nothing is playing. Pushing an identical snapshot over SSE every
        # tick made the frontend re-render the playback bar (and everything
        # subscribed to that store) continuously, which is a large part of
        # why long lists stuttered. Only publish real changes.
        self._state["playback"] = snapshot
        if snapshot != previous:
            self._publish("playback", snapshot)

    def set_playback_like_state(self, video_id: str, liked: bool, error: str = ""):
        self._publish("playback_like", {"videoId": str(video_id or ""), "liked": bool(liked), "error": str(error or "")})

    # -- out of scope for the MVP: harmless no-ops --------------------------
    def start_camera_stream(self):
        pass

    def stop_camera_stream(self):
        pass

    def start_screen_translate(self, target_lang: str = "es", interval_secs: float = 6.0):
        pass

    def stop_screen_translate(self):
        pass

    def is_screen_translate_active(self) -> bool:
        return False

    def play_movie(self, query: str, kind: str = "movie"):
        self.write_log("Las películas todavía no están disponibles en el modo web — usa --legacy.")

    # -- startup gate ---------------------------------------------------------
    def wait_for_api_key(self):
        """Keep the agent worker idle until the first-run screen saves a key.

        The webview and HTTP server run independently on the main thread.
        Starting Gemini earlier crashes the worker on a clean installation.
        """
        while not self._has_api_key():
            time.sleep(0.25)

    def _has_api_key(self) -> bool:
        if not API_FILE.exists():
            return False
        try:
            import json
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    # -- simple properties (duck-type JarvisUI's) ----------------------------
    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, v: bool):
        self._muted = bool(v)
        # Mirrors ui/__init__.py's _toggle_mute/_apply_mic_available: only
        # persisted when the user opted into "recordar último estado" —
        # otherwise mic_default_state ("on"/"off") always wins on next boot.
        try:
            from actions import app_settings
            if app_settings.get("mic_default_state", "on") == "remember":
                app_settings.set("mic_last_state", "off" if self._muted else "on")
        except Exception:
            pass

    @property
    def current_file(self) -> str | None:
        return None

    @property
    def on_interrupt(self):
        return self.interrupt_callback

    @on_interrupt.setter
    def on_interrupt(self, cb):
        # main.py assigns via `ui.on_interrupt = ...` (mirrors JarvisUI's
        # own property, ui/__init__.py:2308-2314) — without this property
        # that assignment silently created a same-named shadow attribute
        # instead of updating interrupt_callback, and Esc-to-interrupt
        # would call a callback that was never actually set.
        self.interrupt_callback = cb

    def fire_interrupt(self):
        cb = self.interrupt_callback
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
