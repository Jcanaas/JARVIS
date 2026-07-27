"""Tiny JSON-backed application settings store.

Persists user preferences to ``%LOCALAPPDATA%\\Jarvis\\config\\app_settings.json``
(writable location, survives reinstalls — see actions.paths). Thread-safe and
fail-soft: any read/write error falls back to in-memory defaults so the app
never crashes over a settings file.

WARNING for anyone chasing a "settings don't save" report: this has burned us
twice already —
  1. A stale PyInstaller build (``dist/Jarvis/Jarvis.exe``) running old code
     while the source already had the fix. Check the exe's build date against
     this file's last edit before assuming the bug is still live.
  2. The real one: ``%LOCALAPPDATA%\\Jarvis`` can end up with an ACL that
     only grants Read+Execute (no write) to whatever Windows account is
     actually running the app (seen with a sandboxed test account). Every
     write then fails with PermissionError, and the fail-soft `except
     Exception: pass` below swallows it — settings silently never persist,
     with zero error anywhere. If this happens again, `icacls
     "%LOCALAPPDATA%\Jarvis"` first, don't just re-read this module's code.
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
    "proactive_enabled": False,
    "proactive_interval_minutes": 15,
    "proactive_prompt": "",
    "voice_journal_enabled": True,
    "voice_journal_hour": 21,
    "voice_journal_last_date": "",
    "morning_dashboard_enabled": True,
    "right_panel_collapsed": False,
    # Non-sensitive autofill data for repetitive web forms (name/email/address/
    # phone only — never passwords, cards, or IDs; see agent/browser_agent.py).
    "form_profile": {},
}


_BACKUP_FILE = None  # set lazily below, mirrors _FILE's directory


def _backup_path():
    global _BACKUP_FILE
    if _BACKUP_FILE is None:
        _BACKUP_FILE = _FILE.with_suffix(".json.bak")
    return _BACKUP_FILE


def _load_locked() -> dict:
    """Load settings from disk. Falls back to the last-good backup if the
    primary file is missing/corrupt (e.g. app was killed mid-write) instead of
    silently discarding every saved setting — that used to reset ALL app
    settings to defaults with no warning whenever a write got interrupted."""
    global _cache
    if _cache is None:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except Exception:
            try:
                data = json.loads(_backup_path().read_text(encoding="utf-8"))
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
    """Store key=value and flush to disk (best effort).

    Writes atomically (temp file + os.replace) and keeps a .bak copy of the
    last known-good file. Do NOT switch this back to a direct write_text():
    a crash/kill mid-write can truncate the file, and without the backup
    _load_locked() has nothing to recover from — that silently reset every
    saved setting to defaults in the past.
    """
    with _lock:
        data = _load_locked()
        data[key] = value
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            if _FILE.exists():
                try:
                    _FILE.replace(_backup_path())
                except Exception:
                    pass
            tmp = _FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(_FILE)
        except Exception as exc:
            # Deliberately fail-soft (never crash the app over a settings
            # write) but NEVER swallow this silently again — a PermissionError
            # here (e.g. a locked-down ACL on %LOCALAPPDATA%\Jarvis) used to
            # look exactly like "settings just don't save" with no trace
            # anywhere. See module docstring.
            print(f"[app_settings] failed to persist '{key}': {exc!r}", file=sys.stderr)


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
