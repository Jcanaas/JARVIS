"""«Mi biblioteca» — one list covering both halves of the Juegos mode.

Two very different kinds of thing end up here, so the module keeps them apart
by how they are discovered rather than by pretending they are the same:

- **Registered entries** (``game_library.json``): things Jarvis itself put on
  disk — a downloaded ROM, a finished repack. These are explicit records with a
  path, artwork and play stats, because nothing else on the system knows about
  them.
- **Scanned entries**: Steam/Epic installs, plus any ROM dropped into
  ``roms/<console>/`` by hand. These are re-derived on every call instead of
  being written to the registry, so uninstalling a game elsewhere doesn't leave
  a dead card behind.

``list_entries()`` merges both, de-duplicated by path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from actions.paths import config_path

_REGISTRY_FILE = config_path("game_library.json")
_LOCK = threading.Lock()

_STEAM_HEADER = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg"
_STEAM_CAPSULE = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"

# Steam installs runtime/tooling depots through the same appmanifest mechanism
# as real games, so they show up in a library scan as playable cards.
_STEAM_NON_GAMES = {"228980", "1070560", "1391110", "1493710", "1628350"}
_STEAM_NON_GAME_WORDS = ("redistributable", "steam linux runtime", "proton",
                         "steamvr", "steam controller")


@dataclass
class LibraryEntry:
    """One playable thing the user owns.

    The poster/title/year/rating block mirrors ``steam_catalog.Game`` so the
    shared card widgets render a library entry unchanged.
    """

    entry_id: str
    title: str
    kind: str                 # "rom" | "game" | "steam" | "epic"
    path: str = ""            # ROM file, or install folder for PC games
    console_id: str = ""      # roms only
    platform: str = ""        # human label: "Game Boy Advance", "Steam", …
    poster_url: str = ""
    header_url: str = ""
    backdrop_url: str = ""
    thumb_url: str = ""
    release_year: int = 0
    rating: float = 0.0
    overview: str = ""
    size_bytes: int = 0
    added: float = 0.0
    last_played: float = 0.0
    play_count: int = 0
    launch_id: str = ""       # steam appid / epic app name
    removable: bool = True    # scanned PC installs are not ours to delete

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _load() -> dict:
    try:
        if _REGISTRY_FILE.is_file():
            data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save(reg: dict) -> None:
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_FILE.write_text(json.dumps(reg, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    except Exception:
        pass


def _entry_id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _register(entry: LibraryEntry) -> LibraryEntry:
    with _LOCK:
        reg = _load()
        previous = reg.get(entry.entry_id) or {}
        # Keep play stats across a re-download of the same title.
        entry.added = float(previous.get("added") or entry.added or time.time())
        entry.last_played = float(previous.get("last_played") or 0.0)
        entry.play_count = int(previous.get("play_count") or 0)
        reg[entry.entry_id] = entry.to_dict()
        _save(reg)
    return entry


def add_rom(rom, path: str | Path) -> LibraryEntry:
    """Record a downloaded ROM. `rom` is an ``actions.rom_catalog.Rom``."""
    from actions import rom_catalog as rc

    file_path = Path(path)
    console = rc.CONSOLES.get(getattr(rom, "console_id", ""), None)
    entry = LibraryEntry(
        entry_id=_entry_id("rom", getattr(rom, "stem", "") or file_path.stem),
        title=getattr(rom, "title", file_path.stem),
        kind="rom",
        path=str(file_path),
        console_id=getattr(rom, "console_id", ""),
        platform=console.name if console else "Emulador",
        poster_url=getattr(rom, "poster_url", ""),
        header_url=getattr(rom, "header_url", ""),
        backdrop_url=getattr(rom, "backdrop_url", ""),
        thumb_url=getattr(rom, "thumb_url", ""),
        overview=getattr(rom, "region", ""),
        size_bytes=file_path.stat().st_size if file_path.is_file() else 0,
        added=time.time(),
    )
    return _register(entry)


def add_local_rom(path: str | Path, console_id: str, title: str = "") -> LibraryEntry:
    """Register a ROM/disc image the user already owns."""
    from actions import rom_catalog as rc

    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"No existe el archivo: {file_path}")
    console = rc.CONSOLES.get(console_id)
    entry = LibraryEntry(
        entry_id=_entry_id("rom", file_path.stem),
        title=title or file_path.stem,
        kind="rom",
        path=str(file_path),
        console_id=console_id,
        platform=console.name if console else console_id.upper(),
        size_bytes=file_path.stat().st_size,
        added=time.time(),
    )
    return _register(entry)


def add_game(title: str, path: str | Path, *, poster_url: str = "",
             header_url: str = "", platform: str = "PC") -> LibraryEntry:
    """Record a downloaded PC game (repack) so it shows up next to the ROMs."""
    entry = LibraryEntry(
        entry_id=_entry_id("game", str(title).strip().lower()),
        title=title,
        kind="game",
        path=str(path),
        platform=platform,
        poster_url=poster_url,
        header_url=header_url,
        backdrop_url=header_url,
        added=time.time(),
    )
    return _register(entry)


def remove(entry_id: str, delete_files: bool = False) -> bool:
    with _LOCK:
        reg = _load()
        entry = reg.pop(entry_id, None)
        _save(reg)
    if entry and delete_files and entry.get("path"):
        target = Path(entry["path"])
        try:
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                import shutil
                shutil.rmtree(target, ignore_errors=True)
        except Exception:
            pass
    return entry is not None


def mark_played(entry_id: str) -> None:
    with _LOCK:
        reg = _load()
        entry = reg.get(entry_id)
        if not entry:
            return
        entry["last_played"] = time.time()
        entry["play_count"] = int(entry.get("play_count") or 0) + 1
        _save(reg)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_roms() -> list[LibraryEntry]:
    """ROM files sitting in ``roms/<console>/`` that the registry doesn't know.

    Copying a .gba into the folder by hand is a perfectly normal way to add a
    game, so those files get first-class cards without a download having to
    have happened inside Jarvis.
    """
    from actions import rom_catalog as rc

    out: list[LibraryEntry] = []
    for console in rc.CONSOLES.values():
        folder = rc.roms_dir(console.id)
        try:
            files = sorted(folder.iterdir())
        except OSError:
            continue
        for file_path in files:
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in console.rom_exts:
                continue
            stem = file_path.stem
            boxart, title_screen, snap = rc.artwork_urls(console.id, stem)
            region, _langs, _rev = rc._parse_tags(stem)
            out.append(LibraryEntry(
                entry_id=_entry_id("rom", stem),
                title=rc._clean_title(stem),
                kind="rom",
                path=str(file_path),
                console_id=console.id,
                platform=console.name,
                poster_url=boxart,
                header_url=title_screen,
                backdrop_url=snap,
                thumb_url=snap,
                overview=region,
                size_bytes=file_path.stat().st_size,
            ))
    return out


def scan_pc_games() -> list[LibraryEntry]:
    """Steam and Epic installs, read from their own manifests."""
    out: list[LibraryEntry] = []
    try:
        from actions import game_updater as gu
    except Exception:
        return out

    try:
        steam_path = gu._find_steam_path()
        if steam_path:
            for game in gu._get_steam_games(steam_path):
                appid = str(game.get("id") or "")
                name = game.get("name") or appid
                if appid in _STEAM_NON_GAMES or any(
                    word in name.lower() for word in _STEAM_NON_GAME_WORDS
                ):
                    continue
                out.append(LibraryEntry(
                    entry_id=_entry_id("steam", appid),
                    title=name,
                    kind="steam",
                    path=game.get("lib") or "",
                    platform="Steam",
                    poster_url=_STEAM_HEADER.format(appid=appid),
                    header_url=_STEAM_CAPSULE.format(appid=appid),
                    backdrop_url=_STEAM_CAPSULE.format(appid=appid),
                    size_bytes=int(game.get("size") or 0),
                    launch_id=appid,
                    removable=False,
                ))
    except Exception:
        pass

    try:
        for game in gu._get_epic_games():
            name = game.get("name") or game.get("app") or ""
            if not name:
                continue
            out.append(LibraryEntry(
                entry_id=_entry_id("epic", str(game.get("app") or name)),
                title=name,
                kind="epic",
                path=game.get("path") or "",
                platform="Epic Games",
                launch_id=str(game.get("app") or ""),
                removable=False,
            ))
    except Exception:
        pass
    return out


def list_entries(include_pc: bool = True) -> list[LibraryEntry]:
    """Everything the user can play, newest activity first."""
    with _LOCK:
        reg = _load()

    entries: dict[str, LibraryEntry] = {}
    for entry_id, raw in reg.items():
        try:
            raw = dict(raw)
            raw.pop("entry_id", None)
            entries[entry_id] = LibraryEntry(entry_id=entry_id, **raw)
        except Exception:
            continue

    # Registered ROMs whose file was deleted outside Jarvis are stale cards.
    for entry_id in list(entries):
        entry = entries[entry_id]
        if entry.kind == "rom" and entry.path and not Path(entry.path).is_file():
            entries.pop(entry_id)

    scanned = scan_roms() + (scan_pc_games() if include_pc else [])
    for entry in scanned:
        if entry.entry_id not in entries:
            entries[entry.entry_id] = entry

    ordered = sorted(
        entries.values(),
        key=lambda e: (e.last_played or e.added or 0.0, e.title.lower()),
        reverse=True,
    )
    return ordered


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def launch(entry: LibraryEntry):
    """Play a library entry outside the UI: external emulator, or store protocol.

    The Emuladores tab does *not* come through here for ROMs — it plays them on
    its own embedded libretro core. This branch is the escape hatch for callers
    with no panel (the AI assistant, a future CLI) and needs a full emulator
    application installed.
    """
    if entry.kind == "rom":
        from actions import emulator_runtime as er
        proc = er.launch(entry.path, entry.console_id or "gba")
        mark_played(entry.entry_id)
        return proc

    if entry.kind == "steam" and entry.launch_id:
        _open_url(f"steam://rungameid/{entry.launch_id}")
        mark_played(entry.entry_id)
        return None

    if entry.kind == "epic" and entry.launch_id:
        _open_url(
            "com.epicgames.launcher://apps/"
            f"{entry.launch_id}?action=launch&silent=true"
        )
        mark_played(entry.entry_id)
        return None

    # Downloaded repack: there is no reliable way to know which .exe is the
    # game, so open the folder and let the user pick (or run the installer).
    target = Path(entry.path)
    if target.exists():
        open_location(entry)
        mark_played(entry.entry_id)
        return None
    raise RuntimeError(f"No sé cómo lanzar «{entry.title}»")


def open_location(entry: LibraryEntry) -> None:
    target = Path(entry.path)
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        return
    try:
        if os.name == "nt":
            os.startfile(str(target))  # noqa: S606 - user-initiated folder open
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception:
        pass


def _open_url(url: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(url)  # noqa: S606 - store protocol handler
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        pass
