"""Centralised filesystem paths for Jarvis.

Works both when running from source and when frozen with PyInstaller, and keeps
all user-writable data outside the (potentially read-only) install directory.

- RESOURCE_DIR : read-only files shipped with the app (prompt.txt, mpv, icons,
  the whatsapp_bridge JS, default config/memory templates). When frozen this is
  ``sys._MEIPASS`` (onefile) or the folder next to the executable (onefolder);
  from source it is the project root.
- DATA_DIR     : user-writable data, ``%LOCALAPPDATA%\\Jarvis`` on Windows.
  Holds config/ (tokens, api keys), memory/, logs/ and the WhatsApp session.

On import the data directories are created and, on first run, seeded from any
defaults bundled under RESOURCE_DIR (existing user data is never overwritten).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    # From source: this file lives at <root>/actions/paths.py
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(root) / "Jarvis"
    return Path(os.path.expanduser("~")) / ".jarvis"


RESOURCE_DIR = _resource_dir()
DATA_DIR     = _data_dir()
CONFIG_DIR   = DATA_DIR / "config"
MEMORY_DIR   = DATA_DIR / "memory"
LOGS_DIR     = DATA_DIR / "logs"
WHATSAPP_DIR = DATA_DIR / "whatsapp_bridge"


def resource(*parts) -> Path:
    """Path to a read-only bundled resource."""
    return RESOURCE_DIR.joinpath(*parts)


def config_path(*parts) -> Path:
    """Path inside the writable config directory."""
    return CONFIG_DIR.joinpath(*parts)


def memory_path(*parts) -> Path:
    """Path inside the writable memory directory."""
    return MEMORY_DIR.joinpath(*parts)


def _ensure_dirs() -> None:
    for d in (CONFIG_DIR, MEMORY_DIR, LOGS_DIR, WHATSAPP_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def _seed_dir(src: Path, dest: Path) -> None:
    """Copy *.json defaults from src→dest without overwriting user data."""
    if not src.is_dir() or src.resolve() == dest.resolve():
        return
    for f in src.glob("*.json"):
        target = dest / f.name
        if not target.exists():
            try:
                shutil.copyfile(f, target)
            except Exception:
                pass


def init_data_dir() -> None:
    _ensure_dirs()
    _seed_dir(RESOURCE_DIR / "config", CONFIG_DIR)
    _seed_dir(RESOURCE_DIR / "memory", MEMORY_DIR)


# Initialise eagerly so every importer sees ready directories.
init_data_dir()
