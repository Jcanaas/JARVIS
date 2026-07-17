"""Point python-vlc at the VLC runtime bundled with the app (if present).

The build copies libvlc.dll + libvlccore.dll + plugins/ into RESOURCE_DIR/vlc
(build/build.ps1). python-vlc loads libvlc.dll at import time, so setup() MUST
run before ``import vlc``. Without a bundle it does nothing and python-vlc
falls back to its normal lookup (registry/PATH — a system VLC install).
"""
from __future__ import annotations

import os

from actions.paths import RESOURCE_DIR


def setup() -> None:
    vlc_dir = RESOURCE_DIR / "vlc"
    lib = vlc_dir / "libvlc.dll"
    if not lib.is_file():
        return
    # python-vlc honours PYTHON_VLC_LIB_PATH (full path to libvlc.dll); the
    # plugin dir must be explicit too or libvlc scans relative to the process.
    os.environ.setdefault("PYTHON_VLC_LIB_PATH", str(lib))
    plugins = vlc_dir / "plugins"
    if plugins.is_dir():
        os.environ.setdefault("VLC_PLUGIN_PATH", str(plugins))
    try:
        # So ctypes resolves libvlccore.dll next to libvlc.dll.
        os.add_dll_directory(str(vlc_dir))
    except (AttributeError, OSError):
        pass
