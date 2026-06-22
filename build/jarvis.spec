# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Jarvis (Mark-XXXIX).

One-folder build. User-writable data is NOT bundled — the app creates and seeds
%LOCALAPPDATA%\\Jarvis at runtime (see actions/paths.py). The embedded Node
runtime is copied into the dist by build/build.ps1 after this spec runs.

Build from the project root:
    pyinstaller build/jarvis.spec --noconfirm
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# The spec runs with CWD = project root (build.ps1 ensures this).
ROOT = Path(os.path.abspath(os.getcwd()))


# --- Read-only resources bundled next to the code (land under _internal/) -----
datas = [
    (str(ROOT / "core" / "prompt.txt"), "core"),
]

# Ship the developer's OAuth client so end users sign in without creating their
# own Google Cloud project. Seeded to %LOCALAPPDATA%\Jarvis\config on first run
# (actions/paths.py). NOTE: bundle ONLY the client secret — never tokens.
_creds = ROOT / "config" / "google_credentials.json"
if _creds.is_file():
    datas.append((str(_creds), "config"))

binaries = []
for name in ("mpv.exe", "mpv.com", "d3dcompiler_43.dll"):
    p = ROOT / name
    if p.is_file():
        binaries.append((str(p), "."))

if (ROOT / "7z" / "7zr.exe").is_file():
    datas.append((str(ROOT / "7z" / "7zr.exe"), "7z"))


def _add_tree(folder: str, dest: str, skip=()):
    base = ROOT / folder
    if not base.is_dir():
        return
    for f in base.rglob("*"):
        if f.is_file() and not any(part in skip for part in f.parts):
            rel = f.parent.relative_to(base)
            datas.append((str(f), str(Path(dest) / rel)))


# WhatsApp Node bridge incl. its node_modules (read-only JS bundle).
_add_tree("whatsapp_bridge", "whatsapp_bridge",
          skip=(".wwebjs_auth", ".wwebjs_cache", "bridge_token",
                "bridge_state.json", "bridge.log"))


# --- Dynamic / data-heavy third-party packages --------------------------------
hiddenimports = []
_collect_pkgs = [
    "googleapiclient", "google_auth_oauthlib", "google.auth", "google.oauth2",
    "google.generativeai", "google.genai",
    "yt_dlp", "qrcode", "comtypes", "pycaw", "duckduckgo_search",
    "youtube_transcript_api", "pptx", "dateutil", "bs4",
]
for pkg in _collect_pkgs:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # package not installed / not collectable
        print(f"[spec] collect_all skipped {pkg}: {exc}")

# Modules imported lazily/dynamically that the analysis can miss.
for mod in ("comtypes.stream", "win32timezone"):
    try:
        hiddenimports += collect_submodules(mod)
    except Exception:
        pass


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6", "PyQt5"],
    noarchive=False,
)

pyz = PYZ(a.pure)

_icon = ROOT / "installer" / "mpv-icon.ico"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app — no console window
    icon=str(_icon) if _icon.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Jarvis",
)
