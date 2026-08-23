"""Emulator runtimes for the Emuladores tab — cores first, apps as a fallback.

There are two ways to run a ROM here, and they are not equals:

- **Embedded (the default).** A *libretro core* is a plain DLL that hands the
  host a framebuffer once per frame and asks it for input — no window, no
  process, no UI of its own. ``install_core`` fetches ``mgba_libretro.dll``
  from libretro's buildbot and actions/libretro.py drives it, so the game
  renders inside a Qt widget exactly like the video player does. This is the
  path the panel uses.
- **External (escape hatch).** A full emulator application, detected if the
  user already has one. mGBA and VBA-M expose no embedding API — no equivalent
  of libVLC's ``set_hwnd`` — so these always open their own window. Kept for
  the cases the core can't cover (link cable, the emulator's own debugger).

The core is a ~1 MB download and needs no BIOS for GBA, which is why it can be
fetched on demand instead of shipping in the build.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from actions.paths import DATA_DIR, RESOURCE_DIR

_TIMEOUT = 30
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/1.0"}


class EmulatorError(RuntimeError):
    """Raised when an emulator cannot be found, installed or started."""


# ---------------------------------------------------------------------------
# Libretro cores (the embedded path)
# ---------------------------------------------------------------------------

_BUILDBOT = "https://buildbot.libretro.com/nightly/{platform}/latest/{name}.zip"


@dataclass
class CoreSpec:
    key: str
    name: str            # shown in the UI
    library: str         # base filename without the platform extension
    console_name: str
    # Core options forced on load (RETRO_ENVIRONMENT_GET_VARIABLE). Anything
    # not listed keeps the core's own default.
    options: dict = field(default_factory=dict)


# One core per console, all software-rendered and none needing a BIOS — the
# two constraints that keep "install emulator" a single click. Accuracy-first
# cores are preferred where the speed cost is irrelevant at these resolutions.
CORES: dict[str, CoreSpec] = {
    "gba": CoreSpec(
        key="gba",
        name="mGBA (core libretro)",
        library="mgba_libretro",
        console_name="Game Boy Advance",
    ),
    "snes": CoreSpec(
        key="snes",
        name="Snes9x (core libretro)",
        library="snes9x_libretro",
        console_name="Super Nintendo",
    ),
    "nes": CoreSpec(
        key="nes",
        name="Nestopia UE (core libretro)",
        library="nestopia_libretro",
        console_name="Nintendo Entertainment System",
    ),
    "md": CoreSpec(
        key="md",
        name="Genesis Plus GX (core libretro)",
        library="genesis_plus_gx_libretro",
        console_name="Sega Mega Drive",
    ),
    # Several consoles share one core, and that is not a shortcut: Gambatte is
    # a Game Boy *and* Game Boy Color emulator, and Genesis Plus GX covers the
    # whole 8/16-bit Sega line. Pointing both ids at the same library means the
    # second console needs no download at all.
    "gb": CoreSpec(
        key="gb",
        name="Gambatte (core libretro)",
        library="gambatte_libretro",
        console_name="Game Boy",
    ),
    "gbc": CoreSpec(
        key="gbc",
        name="Gambatte (core libretro)",
        library="gambatte_libretro",
        console_name="Game Boy Color",
    ),
    "sms": CoreSpec(
        key="sms",
        name="Genesis Plus GX (core libretro)",
        library="genesis_plus_gx_libretro",
        console_name="Sega Master System",
    ),
    "gg": CoreSpec(
        key="gg",
        name="Genesis Plus GX (core libretro)",
        library="genesis_plus_gx_libretro",
        console_name="Sega Game Gear",
    ),
    "pce": CoreSpec(
        key="pce",
        name="Mednafen PCE Fast (core libretro)",
        library="mednafen_pce_fast_libretro",
        console_name="PC Engine / TurboGrafx-16",
    ),
    "ngpc": CoreSpec(
        key="ngpc",
        name="Mednafen NGP (core libretro)",
        library="mednafen_ngp_libretro",
        console_name="Neo Geo Pocket Color",
    ),
    # N64 is the one console here that normally needs a GPU. ParaLLEl N64
    # bundles Angrylion, a pure-software RDP, and selecting it is what keeps
    # this core inside the software-only contract actions/libretro.py enforces
    # — with the default GL plugin the core asks for SET_HW_RENDER, gets a
    # refusal, and renders nothing.
    "n64": CoreSpec(
        key="n64",
        name="ParaLLEl N64 · Angrylion (core libretro)",
        library="parallel_n64_libretro",
        console_name="Nintendo 64",
        options={
            "parallel-n64-gfxplugin": "angrylion",
            "parallel-n64-angrylion-multithread": "all threads",
        },
    ),
    # PS2 scenes overwhelm the CPU rasterizer even at native resolution
    # (Madagascar gameplay measured 11–14 fps on an 8-core Ryzen). The same
    # core settles around 3.3 ms/frame with OpenGL after its initial shader
    # compilation, so hardware rendering is the usable default. The frontend
    # owns the offscreen GL context and reads the native-resolution frame back
    # for the embedded QWidget.
    "ps2": CoreSpec(
        key="ps2",
        name="PCSX2 (core libretro) · sin verificar",
        library="pcsx2_libretro",
        console_name="PlayStation 2",
        options={
            "pcsx2_renderer": "OpenGL",
            "pcsx2_upscale_multiplier": "1x Native (PS2)",
        },
    ),
}


# What each console's pad actually has, so a touch controller can draw the
# right thing instead of a lowest-common-denominator D-pad. Everything not
# listed is a purely digital pad; only the exceptions are spelled out.
_PAD_LAYOUTS: dict[str, dict] = {
    "ps2":  {"sticks": 2, "triggers": True,  "stick_buttons": True,  "shoulders": True,  "face": "playstation"},
    "n64":  {"sticks": 1, "triggers": True,  "stick_buttons": False, "shoulders": True},
    "gba":  {"shoulders": True},
    "snes": {"shoulders": True},
    "md":   {"shoulders": True},   # 6-button pad: X/Y/Z land on the retropad shoulders
    "pce":  {},
}

_PAD_DEFAULT = {
    "sticks": 0,
    "triggers": False,
    "stick_buttons": False,
    "shoulders": False,
    "face": "nintendo",
}


def pad_layout(console_id: str = "") -> dict:
    """Controller shape for `console_id`, filled in with the digital default."""
    layout = dict(_PAD_DEFAULT)
    layout.update(_PAD_LAYOUTS.get(str(console_id or "").lower(), {}))
    layout["console"] = str(console_id or "")
    return layout


def _buildbot_platform() -> tuple[str, str]:
    """(buildbot path, shared-library suffix) for the running machine."""
    machine = platform.machine().lower()
    if os.name == "nt":
        return ("windows/x86_64" if machine in ("amd64", "x86_64")
                else "windows/x86", ".dll")
    if sys.platform == "darwin":
        return ("apple/osx/arm64" if machine == "arm64"
                else "apple/osx/x86_64", ".dylib")
    if machine in ("aarch64", "arm64"):
        return "linux/arm64", ".so"
    return "linux/x86_64", ".so"


def cores_dir() -> Path:
    d = DATA_DIR / "emulators" / "cores"
    d.mkdir(parents=True, exist_ok=True)
    return d


def system_dir() -> Path:
    """Where cores look for BIOS files. GBA needs none, GB/GBC neither."""
    d = DATA_DIR / "emulators" / "system"
    d.mkdir(parents=True, exist_ok=True)
    return d


def saves_dir() -> Path:
    """Battery saves (.srm) and quick save states (.state)."""
    d = DATA_DIR / "emulators" / "saves"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_core(console_id: str = "gba") -> Optional[Path]:
    spec = CORES.get(console_id)
    if spec is None:
        return None
    _, suffix = _buildbot_platform()
    filename = spec.library + suffix
    for root in (cores_dir(), RESOURCE_DIR / "emulators" / "cores"):
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


def has_core(console_id: str = "gba") -> bool:
    return find_core(console_id) is not None


def install_core(console_id: str = "gba",
                 progress: Optional[Callable[[float, int, int], None]] = None) -> Path:
    """Download the libretro core for a console into DATA_DIR."""
    spec = CORES.get(console_id)
    if spec is None:
        raise EmulatorError(f"No hay core para «{console_id}»")

    plat, suffix = _buildbot_platform()
    filename = spec.library + suffix
    url = _BUILDBOT.format(platform=plat, name=filename)

    buffer = io.BytesIO()
    try:
        with requests.get(url, headers=_UA, stream=True, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            for chunk in resp.iter_content(chunk_size=128 * 1024):
                if not chunk:
                    continue
                buffer.write(chunk)
                done += len(chunk)
                if progress:
                    progress((done / total) if total else 0.0, done, total)
    except Exception as exc:
        raise EmulatorError(f"Fallo la descarga del core: {exc}") from exc

    target_dir = cores_dir()
    try:
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as zf:
            members = [m for m in zf.namelist() if m.endswith(suffix)]
            if not members:
                raise EmulatorError("El paquete del core no trae la librería")
            zf.extract(members[0], target_dir)
            extracted = target_dir / members[0]
    except EmulatorError:
        raise
    except Exception as exc:
        raise EmulatorError(f"No pude descomprimir el core: {exc}") from exc

    target = target_dir / filename
    if extracted != target:
        extracted.replace(target)
    return target


def core_status(console_id: str = "gba") -> dict:
    spec = CORES.get(console_id)
    path = find_core(console_id)
    return {
        "available": path is not None,
        "name": spec.name if spec else "",
        "path": str(path) if path else "",
        "console": spec.console_name if spec else console_id.upper(),
    }


# ---------------------------------------------------------------------------
# External emulator applications (the escape hatch)
# ---------------------------------------------------------------------------

@dataclass
class EmulatorSpec:
    key: str
    name: str
    exe_names: tuple[str, ...]
    # GitHub "owner/repo" + a substring that identifies the Windows zip asset.
    # None means "detect only, never auto-install" (see mGBA above).
    repo: str = ""
    asset_hint: str = ""
    # Extra folders to probe beyond the bundled/installed locations.
    extra_dirs: tuple[str, ...] = ()
    launch_args: tuple[str, ...] = field(default_factory=tuple)


EMULATORS: dict[str, list[EmulatorSpec]] = {
    "gba": [
        EmulatorSpec(
            key="mgba",
            name="mGBA",
            exe_names=("mGBA.exe", "mgba-qt.exe", "mgba.exe", "mgba-qt", "mgba"),
            extra_dirs=("mGBA", "mgba"),
        ),
        EmulatorSpec(
            key="vbam",
            name="VisualBoyAdvance-M",
            exe_names=("visualboyadvance-m.exe", "visualboyadvance-m"),
            repo="visualboyadvance-m/visualboyadvance-m",
            asset_hint="Win-x86_64.zip",
            extra_dirs=("visualboyadvance-m", "VisualBoyAdvance-M"),
        ),
    ],
}


def install_dir(spec: EmulatorSpec) -> Path:
    """Where ``install()`` puts this emulator (user-writable, survives updates)."""
    return DATA_DIR / "emulators" / spec.key


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _find_exe_in(root: Path, spec: EmulatorSpec) -> Optional[Path]:
    if not root.is_dir():
        return None
    for name in spec.exe_names:
        direct = root / name
        if direct.is_file():
            return direct
    # Release zips usually unpack into a single versioned subfolder.
    for name in spec.exe_names:
        try:
            found = next(root.glob(f"*/{name}"), None) or next(root.glob(f"*/*/{name}"), None)
        except OSError:
            found = None
        if found is not None:
            return found
    return None


def _candidate_roots(spec: EmulatorSpec) -> list[Path]:
    roots = [install_dir(spec), RESOURCE_DIR / "emulators" / spec.key]
    if os.name == "nt":
        program_dirs = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        for base in filter(None, program_dirs):
            for sub in spec.extra_dirs:
                roots.append(Path(base) / sub)
    else:
        for sub in spec.extra_dirs:
            roots.append(Path("/usr/share") / sub)
    return roots


def find_emulator(console_id: str = "gba") -> Optional[tuple[EmulatorSpec, Path]]:
    """First usable emulator for a console, in preference order."""
    for spec in EMULATORS.get(console_id, []):
        for root in _candidate_roots(spec):
            exe = _find_exe_in(root, spec)
            if exe is not None:
                return spec, exe
        for name in spec.exe_names:
            on_path = shutil.which(name)
            if on_path:
                return spec, Path(on_path)
    return None


def is_installed(console_id: str = "gba") -> bool:
    return find_emulator(console_id) is not None


def installer_spec(console_id: str = "gba") -> Optional[EmulatorSpec]:
    """The emulator ``install()`` would fetch for this console."""
    for spec in EMULATORS.get(console_id, []):
        if spec.repo and spec.asset_hint:
            return spec
    return None


def status(console_id: str = "gba") -> dict:
    found = find_emulator(console_id)
    spec = installer_spec(console_id)
    return {
        "installed": found is not None,
        "name": found[0].name if found else "",
        "path": str(found[1]) if found else "",
        "installable": spec.name if spec else "",
    }


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def _resolve_asset(spec: EmulatorSpec) -> tuple[str, str]:
    url = f"https://api.github.com/repos/{spec.repo}/releases/latest"
    try:
        resp = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise EmulatorError(f"No pude consultar la última versión: {exc}") from exc

    for asset in payload.get("assets", []):
        name = asset.get("name") or ""
        # "-debug.zip" builds share the platform hint but are far bigger and
        # not what a player wants.
        if spec.asset_hint in name and "debug" not in name.lower():
            return name, asset.get("browser_download_url") or ""
    raise EmulatorError(f"La última versión de {spec.name} no trae binario de Windows")


def install(console_id: str = "gba",
            progress: Optional[Callable[[float, int, int], None]] = None) -> Path:
    """Download and unpack the auto-installable emulator for a console.

    Returns the executable's path. ``progress`` is (fraction, done, total).
    """
    spec = installer_spec(console_id)
    if spec is None:
        raise EmulatorError(f"No hay emulador instalable para «{console_id}»")

    name, url = _resolve_asset(spec)
    if not url:
        raise EmulatorError(f"No encontré descarga para {spec.name}")

    buffer = io.BytesIO()
    try:
        with requests.get(url, headers=_UA, stream=True, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                buffer.write(chunk)
                done += len(chunk)
                if progress:
                    progress((done / total) if total else 0.0, done, total)
    except Exception as exc:
        raise EmulatorError(f"Fallo la descarga de {spec.name}: {exc}") from exc

    target = install_dir(spec)
    target.mkdir(parents=True, exist_ok=True)
    try:
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as zf:
            zf.extractall(target)
    except Exception as exc:
        raise EmulatorError(f"No pude descomprimir {spec.name}: {exc}") from exc

    exe = _find_exe_in(target, spec)
    if exe is None:
        raise EmulatorError(f"{spec.name} se descomprimió pero no encontré el ejecutable")
    if os.name != "nt":
        try:
            exe.chmod(exe.stat().st_mode | 0o111)
        except OSError:
            pass
    return exe


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def launch(rom_path: str | Path, console_id: str = "gba") -> subprocess.Popen:
    """Start a ROM in its own emulator window.

    The process is detached from Jarvis' console (Windows) so closing the
    emulator never takes the app down and vice-versa.
    """
    rom = Path(rom_path)
    if not rom.is_file():
        raise EmulatorError(f"No existe la ROM: {rom}")

    found = find_emulator(console_id)
    if found is None:
        raise EmulatorError("No hay emulador instalado para esta consola")
    spec, exe = found

    cmd = [str(exe), *spec.launch_args, str(rom)]
    kwargs: dict = {"cwd": str(exe.parent)}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        raise EmulatorError(f"No pude arrancar {spec.name}: {exc}") from exc


def open_install_folder(console_id: str = "gba") -> None:
    found = find_emulator(console_id)
    target = found[1].parent if found else (DATA_DIR / "emulators")
    target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(target))  # noqa: S606 - user-initiated folder open
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception:
        pass
