"""Tiny JSON-backed application settings store.

Persists user preferences to ``%LOCALAPPDATA%\\Jarvis\\config\\app_settings.json``
(writable location, survives reinstalls — see actions.paths). Thread-safe and
fail-soft: any read/write error falls back to in-memory defaults so the app
never crashes over a settings file.

See NOTE_settings_persistence.md at the repo root for the full incident
history of "settings don't save" reports and why this file is written the
way it is. Short version:
  1. A stale PyInstaller build (``dist/Jarvis/Jarvis.exe``) can run old code
     while the source already has a fix — check the exe's build date first.
  2. ``%LOCALAPPDATA%\\Jarvis`` can end up with an ACL that denies write to
     whatever account runs the app; failures were silent (`except: pass`).
     Fixed: failures now print to stderr instead of vanishing.
  3. THE REAL RECURRING ONE: this module used to cache the loaded JSON in a
     module-level dict for the lifetime of the process. If the file on disk
     changed after that cache was populated (another process wrote it, the
     app restarted mid-session, a manual edit, a test script touching the
     real path), the next `set()` call would write the STALE cached dict
     back over disk — silently deleting every key that wasn't in that stale
     copy. This is why isolated settings (crossfade, audio device, panel
     state, whatever) would vanish one at a time instead of all at once.
     Fixed below: `set()` always re-reads the file fresh from disk right
     before merging in the new key, instead of trusting the cache. Do not
     reintroduce a write-time cache — read-modify-write must always start
     from the current on-disk state.
  4. Even with (3) fixed, any one-off script/test/agent session that imports
     this module directly and calls `set()` against the real path still
     writes into the real user's config — harmless for unrelated keys, but a
     real overwrite if it happens to touch a key the user cares about.
     `set()` now refuses to write unless the ``JARVIS_REAL_SESSION`` env var
     is set to "1" — ``main()`` in ``main.py`` sets it as its first action,
     so a genuine running instance of the app (source or frozen) always has
     it. Anything else (a bare `python -c`, a stray script, another agent
     poking the module) does NOT have it and gets a loud stderr warning
     instead of a silent write. Tests must opt in explicitly (see
     tests/test_app_settings.py) — that's intentional, it documents that the
     test really means to exercise a write.
"""
from __future__ import annotations

import json
import os
import sys
import threading

from actions.paths import config_path, RESOURCE_DIR

_FILE = config_path("app_settings.json")
_lock = threading.RLock()
_cache: dict | None = None  # read-path cache only — NEVER used as the base for set()

# Only a real running instance of the app (main.py's main(), source or
# frozen) may persist settings — see module docstring point 4. Anything else
# importing this module (a one-off script, a test, another agent) does not
# have this set and will not silently write into the real user's config.
_REAL_SESSION_ENV = "JARVIS_REAL_SESSION"

# Known settings and their defaults. Unknown keys are still stored/returned.
_DEFAULTS: dict = {
    "crossfade_enabled": False,
    "crossfade_seconds": 3,
    "crossfade_on_skip": False,
    "whatsapp_notifications": True,
    "whatsapp_notification_duration_s": 7,
    # These send message content/audio to Gemini and therefore require an
    # explicit user opt-in. Manual translation/transcription remains available.
    "whatsapp_auto_translate": False,
    "whatsapp_auto_transcribe": False,
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
    # Topic news monitor (actions/background_monitor.py) — daily DDG news
    # check per subscribed topic, dedupe by headline hash.
    "monitor_topics": [],
    "monitor_seen_hashes": {},
    "monitor_last_check_date": "",
    # LAN dashboard (actions/lan_dashboard.py) — opt-in, binds 0.0.0.0.
    # Token is generated once and persisted so a scanned QR keeps working
    # across restarts.
    "lan_dashboard_enabled": False,
    "lan_dashboard_port": 8765,
    "lan_dashboard_token": "",
}


_BACKUP_FILE = None  # set lazily below, mirrors _FILE's directory


def _backup_path():
    global _BACKUP_FILE
    if _BACKUP_FILE is None:
        _BACKUP_FILE = _FILE.with_suffix(".json.bak")
    return _BACKUP_FILE


def _read_disk() -> dict:
    """Read the current on-disk settings, bypassing any in-memory cache.
    Falls back to the .bak file if the primary is missing/corrupt (e.g. the
    app was killed mid-write)."""
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            data = json.loads(_backup_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _load_locked() -> dict:
    """Cached read for `get()`/`all_settings()` — fine to be a little stale
    within a process's lifetime for reads. `set()` does NOT use this; see
    module docstring point 3."""
    global _cache
    if _cache is None:
        _cache = _read_disk()
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

    Always re-reads the file fresh from disk before merging — do NOT change
    this to reuse the module-level cache as the base dict. A stale in-memory
    cache used to get written back over disk verbatim, silently deleting any
    key that wasn't in that stale copy (see module docstring point 3). This
    was the actual cause behind repeated "settings don't save" reports.

    Writes atomically (temp file + os.replace) and keeps a .bak copy of the
    last known-good file, so a crash/kill mid-write can't corrupt the file
    beyond recovery either.
    """
    global _cache
    if os.environ.get(_REAL_SESSION_ENV) != "1":
        print(
            f"[app_settings] refusing to persist '{key}': not inside a real "
            f"Jarvis session (${_REAL_SESSION_ENV} not set). If this is a "
            f"legitimate test, set that env var or patch _FILE directly — "
            f"see tests/test_app_settings.py.",
            file=sys.stderr,
        )
        return
    with _lock:
        data = _read_disk()
        data[key] = value
        _cache = data
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
