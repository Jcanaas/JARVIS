"""Full application reset: wipe ALL user data, then relaunch the app.

Everything Jarvis writes lives under ``DATA_DIR`` (``%LOCALAPPDATA%\\Jarvis`` —
config/tokens/api keys, memory, logs, the WhatsApp session) plus the browser
auth profiles in ``~/.jarvis_profiles``. Some of those files are held open by the
running process (the active log, the WhatsApp bridge session), so deleting them
in-process is unreliable on Windows.

Instead we hand the job to a tiny detached batch script that:
  1. waits until the app has exited and the files unlock,
  2. removes the data directories, and
  3. relaunches the app fresh (so defaults are re-seeded on next start).

The caller is responsible for quitting the Qt app right after invoking
``schedule_reset_and_restart()`` so the batch can take over.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from actions.paths import DATA_DIR, RESOURCE_DIR


def data_locations() -> list[Path]:
    """All on-disk locations that make up "Jarvis data"."""
    return [DATA_DIR, Path(os.path.expanduser("~")) / ".jarvis_profiles"]


def _quote(value) -> str:
    return '"' + str(value).replace('"', "") + '"'


def _relaunch_command() -> str:
    """Quoted command line that starts a fresh copy of the app."""
    prog = sys.executable
    if getattr(sys, "frozen", False):
        args = sys.argv[1:]
    else:
        # Running from source: re-run the interpreter on the original script(s).
        args = sys.argv
    parts = [_quote(prog)] + [_quote(a) for a in args]
    return " ".join(parts)


def _wipe_in_process() -> None:
    """Best-effort delete of whatever isn't locked, before we even exit.

    The batch handles the rest, but clearing the easy files first means a
    smaller window where stale data could be read if a relaunch races ahead.
    """
    import shutil
    for base in data_locations():
        try:
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
        except Exception:
            pass


def schedule_reset_and_restart() -> None:
    """Wipe all user data and relaunch the app.

    On Windows this spawns a hidden, detached batch that deletes the data once
    the current process releases its file handles, then starts the app again.
    The caller must quit the Qt application immediately afterwards.
    """
    # The "start with Windows" toggle lives in the registry (HKCU Run), not in
    # DATA_DIR, so clear it too — otherwise the app would keep auto-starting
    # while the wiped settings show the toggle as off.
    try:
        from actions.app_settings import set_windows_autostart
        set_windows_autostart(False)
    except Exception:
        pass

    _wipe_in_process()

    if os.name != "nt":
        # Non-Windows fallback: data is already wiped best-effort above; just
        # re-exec the interpreter in place.
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            pass
        return

    data = _quote(DATA_DIR)
    profiles = _quote(Path(os.path.expanduser("~")) / ".jarvis_profiles")
    cwd = _quote(RESOURCE_DIR)
    relaunch = _relaunch_command()

    # ``ping`` is used for the inter-try delay because ``timeout`` needs a real
    # console, which a detached/no-window batch does not have.
    script = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set TRIES=0\r\n"
        ":retry\r\n"
        f"rmdir /s /q {data} >nul 2>&1\r\n"
        f"if not exist {data} goto done\r\n"
        "set /a TRIES+=1\r\n"
        "if %TRIES% GEQ 20 goto done\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto retry\r\n"
        ":done\r\n"
        f"rmdir /s /q {profiles} >nul 2>&1\r\n"
        f"start \"\" /d {cwd} {relaunch}\r\n"
        "endlocal\r\n"
        "(goto) 2>nul & del \"%~f0\"\r\n"
    )

    fd, path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_reset_")
    try:
        with os.fdopen(fd, "w", encoding="mbcs", errors="replace") as fh:
            fh.write(script)
    except Exception:
        with open(path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(script)

    creationflags = 0
    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["cmd", "/c", path], creationflags=creationflags, close_fds=True)
