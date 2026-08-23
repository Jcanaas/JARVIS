"""Global (works-without-focus) hotkey via the Win32 RegisterHotKey API,
through ctypes — no extra pip dependency, consistent with the rest of the
repo's native-Windows integrations (comtypes/pywinauto/pycaw).

RegisterHotKey with hWnd=None registers a thread-level hotkey: WM_HOTKEY is
posted to the calling thread's message queue, no window handle needed — so
this just needs a dedicated thread running a GetMessage loop.
"""
from __future__ import annotations

import ctypes
import os
import threading

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312


class GlobalHotkey:
    """Registers one global hotkey and calls `callback` (on a background
    thread — never Qt widgets directly, marshal via a signal) each time it's
    pressed. No-op on non-Windows platforms."""

    def __init__(self, callback, modifiers: int = MOD_CONTROL | MOD_ALT, vk: int = 0x43, hotkey_id: int = 1):
        self._callback = callback
        self._modifiers = modifiers
        self._vk = vk
        self._id = hotkey_id
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if os.name != "nt":
            print("[GlobalHotkey] global hotkeys only supported on Windows")
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, self._id, self._modifiers, self._vk):
            print("[GlobalHotkey] RegisterHotKey failed (combo already taken?)")
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self._callback()
                    except Exception as e:
                        print(f"[GlobalHotkey] callback error: {e}")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, self._id)
