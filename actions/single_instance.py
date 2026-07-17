"""Single-instance guard so only one Jarvis process ever runs at once.

Two autostart paths can fire independently (the installer's Startup-folder
shortcut and the "Iniciar automáticamente" toggle's HKCU Run key), and the
app has no de-dup otherwise — every extra launch used to open a full second
window, WhatsApp bridge and mpv pipeline, which then fought the first
instance over shared resources (named pipes, port 3000) instead of just
being a no-op.

A named Win32 mutex is atomic and needs no server/socket plumbing, so the
check can run before any subsystem starts.
"""
from __future__ import annotations

import ctypes
import sys

_MUTEX_NAME = "Local\\JarvisSingleInstance_v1"
_WINDOW_TITLE_PREFIX = "J.A.R.V.I.S — MARK XXXIX"
_ERROR_ALREADY_EXISTS = 183

_mutex_handle = None  # kept alive for the process lifetime; OS releases it on exit


def is_already_running() -> bool:
    """True if another Jarvis process holds the instance lock.

    Otherwise claims the lock for this process.
    """
    global _mutex_handle
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return kernel32.GetLastError() == _ERROR_ALREADY_EXISTS


def focus_existing_window() -> None:
    """Best-effort: bring an already-running Jarvis window to the front."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0 or not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.startswith(_WINDOW_TITLE_PREFIX):
            found.append(hwnd)
        return True

    try:
        user32.EnumWindows(_enum, 0)
        if found:
            hwnd = found[0]
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
