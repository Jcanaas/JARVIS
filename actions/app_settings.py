"""Tiny JSON-backed application settings store.

Persists user preferences to ``%LOCALAPPDATA%\\Jarvis\\config\\app_settings.json``
(writable location, survives reinstalls — see actions.paths). Thread-safe and
fail-soft: any read/write error falls back to in-memory defaults so the app
never crashes over a settings file.
"""
from __future__ import annotations

import json
import os
import sys
import threading

from actions.paths import config_path, RESOURCE_DIR

_FILE = config_path("app_settings.json")
_lock = threading.RLock()
_cache: dict | None = None

# Known settings and their defaults. Unknown keys are still stored/returned.
_DEFAULTS: dict = {
    "crossfade_enabled": False,
    "crossfade_seconds": 3,
    "crossfade_on_skip": False,
    "whatsapp_notifications": True,
    "whatsapp_notification_duration_s": 7,
}


def _load_locked() -> dict:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except Exception:
            _cache = {}
    return _cache


def get(key: str, default=None):
    """Return the stored value for key, else its registered default, else `default`."""
    with _lock:
        data = _load_locked()
        if key in data:
            return data[key]
    return _DEFAULTS.get(key, default)


def all_settings() -> dict:
    """Return defaults merged with everything currently stored."""
    with _lock:
        merged = dict(_DEFAULTS)
        merged.update(_load_locked())
        return merged


def set(key: str, value) -> None:
    """Store key=value and flush to disk (best effort)."""
    with _lock:
        data = _load_locked()
        data[key] = value
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            _FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


# ── Windows "start with the system" (HKCU Run key) ──────────────────────────
_AUTOSTART_NAME = "JARVIS"


def _autostart_command() -> str:
    """Command line that launches the app, for the Run registry value."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # Running from source: prefer pythonw.exe (no console window) + main.py
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    main_py = str(RESOURCE_DIR / "main.py")
    return f'"{exe}" "{main_py}"'


def set_windows_autostart(enabled: bool) -> bool:
    """Add/remove the app from the current user's Windows startup. Returns success."""
    if os.name != "nt":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        if enabled:
            winreg.SetValueEx(key, _AUTOSTART_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _AUTOSTART_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False
