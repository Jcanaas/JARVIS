"""BIOS files for consoles whose cores can't run without one.

Every console this app supports otherwise needs nothing beyond the ROM and
the core — that is by design (see actions/rom_catalog.py's console-selection
rule). PS2 breaks that rule: PCSX2 (like every real PS2 emulator) requires an
actual Sony BIOS dump to boot anything, cartridge or disc.

That file is copyrighted Sony firmware. This module never fetches, generates
or bundles one — it only accepts a file the user already has (dumped from
their own console) and places it where the core expects to find it. Refusing
to source the file itself is deliberate, not an oversight; see the module's
callers for the user-facing explanation of why.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

# Real Sony PS2 BIOS dumps are, without exception, exactly 4 MiB — every
# region and revision. That makes size a far more reliable detector than
# filename: official dumps are named all kinds of things (SCPH10000.bin,
# ps2-0230a-20080220.bin, USA.bin from various dumping tools...).
_PS2_BIOS_SIZE = 4 * 1024 * 1024
_PS2_EXTS = (".bin", ".rom")


class BiosError(RuntimeError):
    """Raised when a file offered as a BIOS clearly isn't one."""


def find_ps2_bios(system_dir: str | Path) -> Optional[Path]:
    """The first BIOS dump in LRPS2's required ``pcsx2/bios`` folder."""
    folder = Path(system_dir) / "pcsx2" / "bios"
    if not folder.is_dir():
        return None
    try:
        candidates = sorted(folder.iterdir())
    except OSError:
        return None
    for path in candidates:
        if (path.is_file() and path.suffix.lower() in _PS2_EXTS
                and path.stat().st_size == _PS2_BIOS_SIZE):
            return path
    return None


def has_ps2_bios(system_dir: str | Path) -> bool:
    return find_ps2_bios(system_dir) is not None


def import_ps2_bios(source: str | Path, system_dir: str | Path) -> Path:
    """Copy a user-supplied BIOS dump into the core's system directory.

    Validates size strictly rather than trust the extension or filename: a
    wrong file here doesn't fail loudly with a clear message from the core,
    it fails as an unexplained black screen or crash — the same class of
    problem PS2 already has enough of without adding a bad-file case to it.
    """
    src = Path(source)
    if not src.is_file():
        raise BiosError(f"No existe el archivo: {src}")
    size = src.stat().st_size
    if size != _PS2_BIOS_SIZE:
        raise BiosError(
            f"«{src.name}» ocupa {size / 1024:.0f} KB; una BIOS de PS2 real "
            f"ocupa siempre exactamente {_PS2_BIOS_SIZE // 1024} KB. "
            "Esto no parece ser un volcado de BIOS válido."
        )
    folder = Path(system_dir) / "pcsx2" / "bios"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / src.name
    shutil.copyfile(src, target)
    return target


def remove_ps2_bios(system_dir: str | Path) -> bool:
    found = find_ps2_bios(system_dir)
    if found is None:
        return False
    found.unlink()
    return True
