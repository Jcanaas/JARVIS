"""ROM catalogue for the Emuladores tab (metadata + artwork + download).

Mirrors the role actions/steam_catalog.py plays for the Juegos tab: this module
only knows how to *list*, *search* and *fetch* ROMs — running them is
actions/emulator_runtime.py's job, and remembering them is
actions/game_library.py's.

Two public data sources, both open and key-less:

- **Index + download**: an Internet Archive item holding a per-game No-Intro
  set (one .zip per title). The item's file list comes from the public
  metadata API (``/metadata/<id>``) and every file is downloadable from
  ``/download/<id>/<name>``. This replaced Myrient, which was the obvious
  choice until it shut down on 2026-03-31.
- **Artwork**: the libretro-thumbnails GitHub repos, which are keyed by the
  *same* No-Intro filename the index uses (modulo a handful of characters that
  are illegal in filenames), so covers need no matching pass at all.

The index is ~3.5k entries per console, so it is cached on disk and only
re-fetched when the cache is older than _INDEX_TTL.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from actions.paths import DATA_DIR, RESOURCE_DIR

_TIMEOUT = 20
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/1.0"}
_INDEX_TTL = 30 * 24 * 3600  # the underlying No-Intro sets change ~yearly

_ARCHIVE_METADATA = "https://archive.org/metadata/{item}"
_ARCHIVE_DOWNLOAD = "https://archive.org/download/{item}/{name}"
_THUMB_BASE = (
    "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/{kind}/{name}.png"
)

# libretro renames these characters (illegal on Windows / awkward in URLs) to
# "_" when it stores a thumbnail, so the same substitution has to happen here
# for a No-Intro name to resolve to its boxart.
_THUMB_ILLEGAL = '&*/:`<>?\\|"'


class RomCatalogError(RuntimeError):
    """Raised when a ROM index or download fails."""


@dataclass
class Console:
    id: str
    name: str
    short: str
    archive_item: str      # Internet Archive identifier holding the No-Intro set
    thumb_repo: str        # libretro-thumbnails repository name
    rom_exts: tuple[str, ...]
    emulator: str          # key into actions.emulator_runtime.CORES
    # Some Archive items hold several systems in one item, one subfolder each
    # (the Neo Geo Pocket item carries both the mono and Color sets). When set,
    # only files under this folder are indexed.
    archive_prefix: str = ""
    # Large disc sets can be split across several Archive items. Each file row
    # remembers which item it came from so downloads still target one game.
    archive_items: tuple[str, ...] = ()
    # A split item may keep its games below a folder even when the console's
    # main source uses root-level files. Entries are (item identifier, prefix).
    archive_prefixes: tuple[tuple[str, str], ...] = ()
    # Containers used by the item. Cartridge sets use .zip; PS2 Redump stores
    # one independently downloadable .7z per game.
    archive_exts: tuple[str, ...] = (".zip",)
    # Kept for consoles that may only support importing local dumps.
    has_catalog: bool = True


# Adding a console is three verified facts: an Archive item that stores one zip
# per game under its No-Intro name, the matching libretro-thumbnails repo, and a
# core in actions/emulator_runtime.CORES.
#
# Systems whose only cores need hardware rendering (PSX, Saturn, Dreamcast…)
# are deliberately absent: actions/libretro.py refuses
# RETRO_ENVIRONMENT_SET_HW_RENDER, so those cores would load and then never
# produce a frame. N64 is here only because ParaLLEl N64 ships a software RDP
# (see the core options in emulator_runtime.CORES).
CONSOLES: dict[str, Console] = {
    "gba": Console(
        id="gba",
        name="Game Boy Advance",
        short="GBA",
        archive_item="ef_gba_no-intro_2024-02-21",
        thumb_repo="Nintendo_-_Game_Boy_Advance",
        rom_exts=(".gba",),
        emulator="gba",
    ),
    "snes": Console(
        id="snes",
        name="Super Nintendo",
        short="SNES",
        archive_item="ef_nintendo_snes_no-intro_2024-04-20",
        thumb_repo="Nintendo_-_Super_Nintendo_Entertainment_System",
        rom_exts=(".sfc", ".smc", ".swc", ".fig"),
        emulator="snes",
    ),
    "nes": Console(
        id="nes",
        name="Nintendo Entertainment System",
        short="NES",
        archive_item="ef_nintendo_entertainment_-system_-no-intro_2024-04-23",
        thumb_repo="Nintendo_-_Nintendo_Entertainment_System",
        rom_exts=(".nes", ".unf", ".unif"),
        emulator="nes",
    ),
    "md": Console(
        id="md",
        name="Sega Mega Drive",
        short="Mega Drive",
        archive_item="ef_mega_genesis_no-intro_2024-04-21",
        thumb_repo="Sega_-_Mega_Drive_-_Genesis",
        rom_exts=(".md", ".gen", ".bin", ".smd"),
        emulator="md",
    ),
    "gb": Console(
        id="gb",
        name="Game Boy",
        short="Game Boy",
        archive_item="ef_Nintendo_Gameboy_No-Intro_2024-04-23",
        thumb_repo="Nintendo_-_Game_Boy",
        rom_exts=(".gb",),
        emulator="gb",
    ),
    "gbc": Console(
        id="gbc",
        name="Game Boy Color",
        short="Game Boy Color",
        archive_item="ef_GBC_No-Intro",
        thumb_repo="Nintendo_-_Game_Boy_Color",
        rom_exts=(".gbc", ".gb"),
        emulator="gbc",
    ),
    "sms": Console(
        id="sms",
        name="Sega Master System",
        short="Master System",
        archive_item="ef_sms_No-Intro_2024-03-08",
        thumb_repo="Sega_-_Master_System_-_Mark_III",
        rom_exts=(".sms",),
        emulator="sms",
    ),
    "gg": Console(
        id="gg",
        name="Sega Game Gear",
        short="Game Gear",
        archive_item="ef_sega_game_gear_no-intro_2024-02-21",
        thumb_repo="Sega_-_Game_Gear",
        rom_exts=(".gg",),
        emulator="gg",
    ),
    "pce": Console(
        id="pce",
        name="PC Engine / TurboGrafx-16",
        short="PC Engine",
        archive_item="ef_pce_No-Intro_2024",
        thumb_repo="NEC_-_PC_Engine_-_TurboGrafx_16",
        rom_exts=(".pce",),
        emulator="pce",
    ),
    "n64": Console(
        id="n64",
        name="Nintendo 64",
        short="N64",
        archive_item="ef_nintendo_64_no-intro_2024-02-10",
        thumb_repo="Nintendo_-_Nintendo_64",
        rom_exts=(".z64", ".n64", ".v64"),
        emulator="n64",
    ),
    "ps2": Console(
        id="ps2",
        name="PlayStation 2",
        short="PS2",
        archive_item="",
        archive_items=(
            "redumpSonyPlaystation2UsaGames2018Aug01",
            "redumpSonyPlaystation2UsaGames2018Aug01Part2",
            "redumpSonyPlaystation2UsaGames2018Aug01Part3",
            "redumpSonyPlaystation2UsaGames2018Aug01Part4",
            "redumpSonyPlaystation2UsaOther2018Aug01",
            # The first two 2018 items no longer expose files. These newer CHD
            # shards restore the currently public letter ranges.
            "ps2-redump-usa-chd-part-0",
            "ps2-redump-usa-chd-part-B",
            "ps2-redump-usa-chd-part-C",
            "ps2-redump-usa-chd-part-E",
            "ps2-redump-usa-chd-part-F",
            "ps2-redump-usa-chd-part-g_202207",
            "ps2-redump-usa-chd-part-H",
            "ps2-redump-usa-chd-part-I",
            "ps2-redump-usa-chd-part-J",
            "ps2-redump-usa-chd-part-K",
            "ps2-redump-usa-chd-part-L",
            # Kept in the catalogue even though its files are currently
            # restricted. This lets the UI explain why MGS3 is unavailable and
            # offer another public regional release instead of hiding the game.
            "ps2-redump-usa-chd-part-M",
            "ps2-redump-usa-chd-part-P",
            "ps2-redump-usa-chd-part-Q",
            "ps2-redump-usa-chd-part-X",
            "ps2-redump-usa-chd-part-Z",
            # The public D shard is locked. This standalone item keeps the two
            # Madagascar games searchable and individually downloadable.
            "madagascar_20260114",
            # Full European Redump metadata. The files are presently private,
            # but their versions/regions remain useful in the selector.
            "rr-sony-playstation-2",
            "rr-sony-playstation-2-e2",
            "rr-sony-playstation-2-e3",
            # Public standalone PAL release, verified with a ranged download.
            "metal-gear-solid-3-snake-eater-italy-ps2",
            # Public three-disc European Subsistence dump. Archive.org labels
            # the item as "image" and stores the ISOs below DiscImageCreator,
            # so it cannot be discovered through the bulk software sets alone.
            "metal-gear-solid-3-subsistence_202210",
            # Public USA Snake Eater upload packaged as a ZIP.
            "metal-gear-solid-3-snake-eater-usa_202603",
        ),
        archive_prefixes=(
            ("rr-sony-playstation-2", "europe/iso"),
            ("rr-sony-playstation-2-e2", "europe/iso"),
            ("rr-sony-playstation-2-e3", "europe/iso"),
            ("metal-gear-solid-3-subsistence_202210", "DiscImageCreator"),
        ),
        archive_exts=(".7z", ".zip"),
        thumb_repo="Sony_-_PlayStation_2",
        rom_exts=(".iso", ".chd", ".cso", ".zso", ".bin"),
        emulator="ps2",
    ),
    "ngpc": Console(
        id="ngpc",
        name="Neo Geo Pocket Color",
        short="Neo Geo Pocket",
        archive_item="ef_snk_neogeo_Pocket_neogeo_pocket_color_no-intro_2024",
        archive_prefix="SNK - NeoGeo Pocket Color (No-Intro 2024-04-18)",
        thumb_repo="SNK_-_Neo_Geo_Pocket_Color",
        rom_exts=(".ngc", ".ngp"),
        emulator="ngpc",
    ),
}


@dataclass
class Rom:
    """A single ROM.

    The first block of fields deliberately mirrors ``steam_catalog.Game`` /
    ``movie_search.Movie`` so the shared poster-card and hero widgets in
    ui/panels/movies.py can render a Rom without knowing what it is.
    """

    title: str
    poster_url: str = ""
    header_url: str = ""
    backdrop_url: str = ""
    thumb_url: str = ""
    release_year: int = 0
    rating: float = 0.0
    overview: str = ""

    # ROM-specific
    console_id: str = "gba"
    filename: str = ""        # "Metroid - Zero Mission (USA).zip"
    stem: str = ""            # same, without the .zip — the No-Intro name
    size_bytes: int = 0
    download_url: str = ""
    region: str = ""
    languages: list[str] = field(default_factory=list)
    revision: str = ""
    edition: str = ""
    available: bool = True
    unavailable_reason: str = ""

    @property
    def size_label(self) -> str:
        return _human_size(self.size_bytes)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_PAREN_RE = re.compile(r"\(([^()]*)\)")
_REGION_WORDS = {
    "USA": "USA", "Europe": "Europa", "Japan": "Japón", "World": "Mundial",
    "Spain": "España", "France": "Francia", "Germany": "Alemania",
    "Italy": "Italia", "Australia": "Australia", "Korea": "Corea",
    "Brazil": "Brasil", "Canada": "Canadá", "China": "China",
    "Netherlands": "Países Bajos", "Sweden": "Suecia", "Asia": "Asia",
}
# Two-letter ISO-ish codes No-Intro uses inside a "(En,Fr,De,Es,It)" group.
_LANG_CODES = {
    "En", "Fr", "De", "Es", "It", "Nl", "Pt", "Sv", "No", "Da", "Fi",
    "Ja", "Ko", "Zh", "Ru", "Pl", "Cs", "Hu", "El", "Tr", "Ar", "He",
}
# Files the archive item carries alongside the ROMs.
_SKIP_SUFFIXES = (
    "_meta.xml", "_files.xml", "_reviews.xml", "_meta.sqlite",
    "_archive.torrent", ".dat", ".txt", ".jpg", ".png", ".sqlite",
)
_DISPLAY_STEMS = {
    "SLUS_210.15.Madagascar": "DreamWorks Madagascar (USA)",
    "SLUS_218.40.Madagascar_Escape2Africa": (
        "DreamWorks Madagascar - Escape 2 Africa (USA)"
    ),
    "MGS3Subsistance1": (
        "Metal Gear Solid 3 - Subsistence (Europe) (En,Fr) "
        "(Disc 1) (Subsistence Disc)"
    ),
    "MGS3Subsistance2": (
        "Metal Gear Solid 3 - Subsistence (Europe) (En,Fr) "
        "(Disc 2) (Persistence Disc)"
    ),
    "MGS3Subsistance3": (
        "Metal Gear Solid 3 - Subsistence (Europe) (En,Fr) "
        "(Disc 3) (Existence Disc)"
    ),
}


def _clean_title(stem: str) -> str:
    """"Metroid - Zero Mission (USA) (Rev 1)" -> "Metroid - Zero Mission"."""
    cut = stem.find(" (")
    base = stem[:cut] if cut > 0 else stem
    return base.strip() or stem


def _parse_tags(stem: str) -> tuple[str, list[str], str]:
    """Pull (region, languages, revision) out of a No-Intro filename."""
    region, languages, revision = "", [], ""
    for group in _PAREN_RE.findall(stem):
        parts = [p.strip() for p in group.split(",") if p.strip()]
        if not parts:
            continue
        if not region and parts[0] in _REGION_WORDS:
            # Standalone uploads sometimes append the platform to the region,
            # e.g. "(Italy, PS2)". Only actual region words belong in the UI.
            region = ", ".join(_REGION_WORDS[p] for p in parts if p in _REGION_WORDS)
        elif not languages and all(p in _LANG_CODES for p in parts):
            languages = parts
        elif group.startswith("Rev ") or group.startswith("v"):
            revision = group
    return region, languages, revision


def _edition_tags(stem: str) -> str:
    """Return disc/edition tags not already represented by region/language."""
    tags: list[str] = []
    for group in _PAREN_RE.findall(stem):
        parts = [part.strip() for part in group.split(",") if part.strip()]
        if not parts or parts[0] in _REGION_WORDS:
            continue
        if all(part in _LANG_CODES for part in parts):
            continue
        if group.startswith("Rev ") or group.startswith("v"):
            continue
        tags.append(group)
    return " · ".join(tags)


def thumbnail_name(stem: str) -> str:
    """No-Intro name -> the name libretro-thumbnails stores it under."""
    for ch in _THUMB_ILLEGAL:
        stem = stem.replace(ch, "_")
    return stem


def artwork_urls(console_id: str, stem: str) -> tuple[str, str, str]:
    """(boxart, title screen, in-game snap) URLs for a ROM."""
    console = CONSOLES.get(console_id)
    if console is None:
        return "", "", ""
    safe = urllib.parse.quote(thumbnail_name(stem))
    return tuple(  # type: ignore[return-value]
        _THUMB_BASE.format(repo=console.thumb_repo, kind=kind, name=safe)
        for kind in ("Named_Boxarts", "Named_Titles", "Named_Snaps")
    )


def _human_size(n: int) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def cache_dir() -> Path:
    d = DATA_DIR / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def roms_dir(console_id: str = "") -> Path:
    d = DATA_DIR / "roms"
    if console_id:
        d = d / console_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_file(console_id: str) -> Path:
    return cache_dir() / f"roms_{console_id}.json"


def _archive_items(console: Console) -> tuple[str, ...]:
    if console.archive_items:
        return console.archive_items
    return (console.archive_item,) if console.archive_item else ()


def _archive_ext(name: str, console: Console) -> str:
    lower = name.lower()
    return next((ext for ext in console.archive_exts if lower.endswith(ext)), "")


def _source_key(console: Console) -> str:
    return json.dumps(
        {
            "items": _archive_items(console),
            "prefix": console.archive_prefix,
            "prefixes": console.archive_prefixes,
            "archives": console.archive_exts,
            "roms": console.rom_exts,
        },
        sort_keys=True,
    )


def _fetch_index(console: Console) -> list[dict]:
    items = _archive_items(console)
    if not items:
        raise RomCatalogError("Esta consola no tiene una fuente de catálogo")
    item_prefixes = dict(console.archive_prefixes)
    rows: list[dict] = []
    errors: list[str] = []

    def fetch(archive_item: str) -> tuple[str, Optional[dict], Optional[Exception]]:
        url = _ARCHIVE_METADATA.format(item=archive_item)
        try:
            resp = requests.get(url, headers=_UA, timeout=_TIMEOUT * 2)
            resp.raise_for_status()
            return archive_item, resp.json(), None
        except Exception as exc:
            return archive_item, None, exc

    # Disc catalogues are split alphabetically. Fetching their small metadata
    # documents concurrently keeps a cold PS2 load in the same range as the
    # single-item cartridge catalogues.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        payloads = pool.map(fetch, items)

    for archive_item, payload, error in payloads:
        if error is not None or payload is None:
            exc = error or RuntimeError("respuesta vacía")
            errors.append(f"{archive_item}: {exc}")
            continue

        configured_prefix = item_prefixes.get(archive_item, console.archive_prefix)
        prefix = configured_prefix.strip("/") + "/" if configured_prefix else ""
        for entry in payload.get("files", []):
            name = entry.get("name") or ""
            if not name or name.startswith("_"):
                continue
            private = entry.get("private")
            available = not (private is True or str(private).lower() == "true")
            if prefix:
                if not name.startswith(prefix):
                    continue
                leaf = name[len(prefix):]
            else:
                leaf = name
            # Anything still nested belongs to a different system in the same item.
            if not leaf or "/" in leaf:
                continue
            if leaf.lower().endswith(_SKIP_SUFFIXES):
                continue
            container_ext = _archive_ext(leaf, console)
            direct_ext = next(
                (ext for ext in console.rom_exts if leaf.lower().endswith(ext)), ""
            )
            if not container_ext and not direct_ext:
                continue
            source_ext = container_ext or direct_ext
            stem = leaf[:-len(source_ext)]
            try:
                size = int(entry.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            # "name" is the bare filename (what lands on disk); "path" is what the
            # download URL needs, which for a prefixed item includes the folder.
            row = {
                "name": leaf,
                "stem": stem,
                "size": size,
                "archive_item": archive_item,
                "available": available,
                "unavailable_reason": (
                    "Internet Archive mantiene este archivo restringido"
                    if not available else ""
                ),
            }
            if prefix:
                row["path"] = name
            rows.append(row)
    if not rows:
        if errors:
            detail = "; ".join(errors)
            raise RomCatalogError(f"No pude leer el índice de ROMs: {detail}")
        raise RomCatalogError("El índice de ROMs vino vacío")
    # Some old 7z items and newer CHD shards overlap. Keep one card per exact
    # Redump release and prefer a directly playable CHD/ISO over a container.
    unique: dict[str, dict] = {}
    for row in rows:
        key = row["stem"].casefold()
        current = unique.get(key)
        is_direct = any(row["name"].lower().endswith(ext) for ext in console.rom_exts)
        row_rank = (bool(row.get("available", True)), is_direct)
        current_rank = (-1, -1) if current is None else (
            bool(current.get("available", True)),
            any(current["name"].lower().endswith(ext) for ext in console.rom_exts),
        )
        if current is None or row_rank > current_rank:
            unique[key] = row
    rows = list(unique.values())
    rows.sort(key=lambda r: r["stem"].lower())
    return rows


def _load_cached(console_id: str) -> Optional[list[dict]]:
    path = _index_file(console_id)
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        console = CONSOLES.get(console_id)
        if console is None or payload.get("source_key") != _source_key(console):
            return None
        if time.time() - float(payload.get("fetched", 0)) > _INDEX_TTL:
            return None
        rows = payload.get("rows")
        return rows if isinstance(rows, list) and rows else None
    except Exception:
        return None


def _store_cached(console_id: str, rows: list[dict]) -> None:
    try:
        console = CONSOLES[console_id]
        _index_file(console_id).write_text(
            json.dumps({
                "fetched": time.time(),
                "source_key": _source_key(console),
                "rows": rows,
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def _to_rom(console: Console, row: dict) -> Rom:
    stem = row["stem"]
    display_stem = _DISPLAY_STEMS.get(stem, stem)
    region, languages, revision = _parse_tags(display_stem)
    boxart, title_screen, snap = artwork_urls(console.id, display_stem)
    return Rom(
        title=_clean_title(display_stem),
        poster_url=boxart,
        header_url=title_screen,
        backdrop_url=snap,
        thumb_url=snap,
        overview="",
        console_id=console.id,
        filename=row["name"],
        stem=stem,
        size_bytes=int(row.get("size") or 0),
        download_url=_ARCHIVE_DOWNLOAD.format(
            item=row.get("archive_item") or console.archive_item,
            # quote() keeps "/" unescaped, so a prefixed path stays a path.
            name=urllib.parse.quote(row.get("path") or row["name"]),
        ),
        region=region,
        languages=languages,
        revision=revision,
        edition=_edition_tags(display_stem),
        available=bool(row.get("available", True)),
        unavailable_reason=str(row.get("unavailable_reason") or ""),
    )


def get_index(console_id: str = "gba", force: bool = False) -> list[Rom]:
    """Every ROM known for a console. Cached on disk for _INDEX_TTL."""
    console = CONSOLES.get(console_id)
    if console is None:
        raise RomCatalogError(f"Consola desconocida: {console_id}")
    if not console.has_catalog:
        return []
    rows = None if force else _load_cached(console_id)
    if rows is None:
        rows = _fetch_index(console)
        _store_cached(console_id, rows)
    return [_to_rom(console, row) for row in rows]


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def matches_region(rom: Rom, region: str) -> bool:
    """Match a region chip; España also means an explicit Spanish language."""
    if not region:
        return True
    if region == "España" and "Es" in rom.languages:
        return True
    return region.casefold() in rom.region.casefold()


def _variant_rank(rom: Rom) -> tuple:
    """Prefer downloadable and Spanish-friendly variants for a grouped card."""
    if "España" in rom.region:
        region_rank = 0
    elif "Europa" in rom.region:
        region_rank = 1
    elif "USA" in rom.region:
        region_rank = 2
    else:
        region_rank = 3
    return (
        0 if rom.available else 1,
        0 if ("Es" in rom.languages or "España" in rom.region) else 1,
        region_rank,
        1 if rom.revision else 0,
        rom.stem.casefold(),
    )


def _collapse_versions(roms: list[Rom]) -> list[Rom]:
    grouped: dict[tuple[str, str], Rom] = {}
    for rom in roms:
        key = (rom.console_id, _norm(rom.title))
        current = grouped.get(key)
        if current is None or _variant_rank(rom) < _variant_rank(current):
            grouped[key] = rom
    return list(grouped.values())


def versions_for(rom: Rom) -> list[Rom]:
    """All known regional/revision/disc variants of the selected game."""
    key = _norm(rom.title)
    versions = [
        candidate for candidate in get_index(rom.console_id)
        if _norm(candidate.title) == key
    ]
    return sorted(versions, key=_variant_rank)


_LANGUAGE_NAMES = {
    "En": "Inglés", "Es": "Español", "Fr": "Francés", "De": "Alemán",
    "It": "Italiano", "Pt": "Portugués", "Ja": "Japonés",
}


def version_label(rom: Rom) -> str:
    """Compact, user-facing label for the version selector."""
    parts = [rom.region or "Región sin indicar"]
    if rom.languages:
        parts.append(", ".join(_LANGUAGE_NAMES.get(code, code) for code in rom.languages))
    if rom.revision:
        parts.append(rom.revision)
    if rom.edition:
        parts.append(rom.edition)
    if rom.size_bytes:
        parts.append(rom.size_label)
    if not rom.available:
        parts.append("no disponible")
    return " · ".join(parts)


def search(query: str, console_id: str = "gba", region: str = "",
           limit: int = 60) -> list[Rom]:
    """Rank ROMs by how well their title matches `query`.

    An empty query returns the head of the (alphabetical) catalogue, which is
    what the tab shows before the user types anything.
    """
    roms = get_index(console_id)
    if region:
        roms = [r for r in roms if matches_region(r, region)]
    needle = _norm(query)
    if not needle:
        return _collapse_versions(roms)[:limit]

    scored: list[tuple[int, int, Rom]] = []
    for rom in roms:
        haystack = _norm(rom.stem)
        title = _norm(rom.title)
        if needle not in haystack and needle not in title:
            continue
        if title == needle:
            score = 0
        elif title.startswith(needle):
            score = 1
        elif haystack.startswith(needle):
            score = 2
        else:
            score = 3
        scored.append((score, len(rom.stem), rom))
    scored.sort(key=lambda item: (item[0], item[1]))
    return _collapse_versions([rom for _, _, rom in scored])[:limit]


# Curated shelves, keyed by console. The No-Intro index carries no popularity
# signal of any kind (it is a preservation set, not a storefront), so the
# alternative to a hand-picked list is whatever sorts first alphabetically —
# for GBA that is "007" and "2 Game Pack!".
_POPULAR_PICKS: dict[str, list[str]] = {
    "gba": [
        "Pokemon - Emerald Version", "Pokemon - FireRed Version",
        "Pokemon - LeafGreen Version", "Pokemon - Ruby Version",
        "Pokemon - Sapphire Version", "Metroid - Zero Mission",
        "Metroid Fusion", "Castlevania - Aria of Sorrow",
        "Castlevania - Circle of the Moon", "Advance Wars",
        "Advance Wars 2 - Black Hole Rising", "Fire Emblem", "Golden Sun",
        "Golden Sun - The Lost Age", "Mario Kart - Super Circuit",
        "Super Mario Advance", "Legend of Zelda, The - The Minish Cap",
        "Legend of Zelda, The - A Link to the Past & Four Swords",
        "Mother 3", "Kirby - Nightmare in Dream Land",
        "Final Fantasy Tactics Advance", "Megaman Zero",
        "Sonic Advance", "Donkey Kong Country",
        "WarioWare, Inc. - Mega Microgame$",
        "Super Mario Advance 3 - Yoshi's Island",
        "Astro Boy - Omega Factor", "Drill Dozer",
        "Harvest Moon - Friends of Mineral Town", "Rhythm Tengoku",
    ],
    "snes": [
        "Super Mario World", "Super Metroid", "Chrono Trigger",
        "Legend of Zelda, The - A Link to the Past",
        "Super Mario Kart", "Donkey Kong Country",
        "Donkey Kong Country 2 - Diddy's Kong Quest",
        "Final Fantasy III", "Super Mario RPG - Legend of the Seven Stars",
        "EarthBound", "Star Fox", "F-Zero", "Contra III - The Alien Wars",
        "Mega Man X", "Mega Man X2", "Castlevania - Dracula X",
        "Super Castlevania IV", "Secret of Mana", "Terranigma",
        "Illusion of Gaia", "Kirby Super Star",
        "Super Punch-Out!!", "Street Fighter II Turbo",
        "Super Street Fighter II", "Teenage Mutant Ninja Turtles IV - Turtles in Time",
        "ActRaiser", "Demon's Crest", "Tetris Attack", "Harvest Moon",
        "Super Mario World 2 - Yoshi's Island",
    ],
    "nes": [
        "Super Mario Bros.", "Super Mario Bros. 2", "Super Mario Bros. 3",
        "Legend of Zelda, The", "Zelda II - The Adventure of Link",
        "Metroid", "Mega Man 2", "Mega Man 3", "Castlevania",
        "Castlevania III - Dracula's Curse", "Contra", "Super C",
        "Punch-Out!!", "Kirby's Adventure", "DuckTales",
        "Chip 'n Dale Rescue Rangers", "Ninja Gaiden",
        "Teenage Mutant Ninja Turtles II - The Arcade Game",
        "Final Fantasy", "Dragon Warrior", "Excitebike", "Tetris",
        "Bubble Bobble", "Batman - The Video Game", "Blaster Master",
        "River City Ransom", "Kid Icarus", "Adventure Island",
        "Double Dragon II - The Revenge", "Bionic Commando",
    ],
    "md": [
        "Sonic The Hedgehog", "Sonic The Hedgehog 2",
        "Sonic The Hedgehog 3", "Sonic & Knuckles",
        "Streets of Rage 2", "Streets of Rage", "Streets of Rage 3",
        "Gunstar Heroes", "Phantasy Star IV", "Shining Force",
        "Shining Force II", "Golden Axe", "Altered Beast",
        "Ecco the Dolphin", "Castlevania - Bloodlines",
        "Contra - Hard Corps", "Mega Man - The Wily Wars",
        "Rocket Knight Adventures", "Comix Zone", "Vectorman",
        "Ristar", "ToeJam & Earl", "Aladdin", "Earthworm Jim",
        "Road Rash II", "Micro Machines 2 - Turbo Tournament",
        "Thunder Force IV", "Landstalker - The Treasures of King Nole",
        "Story of Thor, The", "Dynamite Headdy",
    ],
    "gb": [
        "Tetris", "Super Mario Land", "Super Mario Land 2 - 6 Golden Coins",
        "Legend of Zelda, The - Link's Awakening",
        "Pokemon - Red Version", "Pokemon - Blue Version",
        "Pokemon - Yellow Version - Special Pikachu Edition",
        "Metroid II - Return of Samus", "Kirby's Dream Land",
        "Kirby's Dream Land 2", "Donkey Kong", "Donkey Kong Land",
        "Wario Land - Super Mario Land 3", "Wario Land II",
        "Mega Man - Dr. Wily's Revenge", "Castlevania - The Adventure",
        "Castlevania II - Belmont's Revenge", "Final Fantasy Adventure",
        "Gargoyle's Quest", "Tetris 2", "Dr. Mario",
        "Kid Icarus - Of Myths and Monsters", "Mole Mania",
        "Game & Watch Gallery", "Bionic Commando", "Operation C",
        "SolarStriker", "Balloon Kid", "R-Type", "Trip World",
    ],
    "gbc": [
        "Legend of Zelda, The - Oracle of Ages",
        "Legend of Zelda, The - Oracle of Seasons",
        "Legend of Zelda, The - Link's Awakening DX",
        "Pokemon - Crystal Version", "Pokemon - Gold Version",
        "Pokemon - Silver Version", "Metal Gear Solid", "Wario Land 3",
        "Super Mario Bros. Deluxe", "Dragon Warrior Monsters", "Shantae",
        "Resident Evil Gaiden", "Donkey Kong Country", "Mario Tennis",
        "Mario Golf", "Tetris DX", "Survival Kids",
        "Alone in the Dark - The New Nightmare", "Conker's Pocket Tales",
        "Crystalis", "Toki Tori", "Grand Theft Auto", "Warlocked",
        "Magi Nation", "Lufia - The Legend Returns", "Harvest Moon 3 GBC",
        "Wario Land II", "Bionic Commando - Elite Forces",
        "Star Wars Episode I - Racer", "Ghosts'n Goblins",
    ],
    "sms": [
        "Sonic The Hedgehog", "Sonic The Hedgehog 2", "Sonic Chaos",
        "Phantasy Star", "Wonder Boy III - The Dragon's Trap",
        "Wonder Boy in Monster Land", "Alex Kidd in Miracle World",
        "Golden Axe", "Shinobi", "Castle of Illusion Starring Mickey Mouse",
        "Land of Illusion Starring Mickey Mouse", "Master of Darkness",
        "Ys - The Vanished Omens", "R-Type", "Fantasy Zone", "Space Harrier",
        "Power Strike II", "Streets of Rage", "Golvellius - Valley of Doom",
        "Psycho Fox", "Ninja Gaiden", "Asterix",
        "Lucky Dime Caper Starring Donald Duck, The", "Alex Kidd in Shinobi World",
        "Rastan", "Double Dragon", "Aladdin", "Gauntlet",
        "Kenseiden", "Impossible Mission",
    ],
    "gg": [
        "Sonic The Hedgehog", "Sonic Chaos", "Sonic the Hedgehog - Triple Trouble",
        "Shining Force - The Sword of Hajya",
        "Shining Force Gaiden - Final Conflict", "Defenders of Oasis",
        "Land of Illusion Starring Mickey Mouse",
        "Castle of Illusion Starring Mickey Mouse", "Streets of Rage",
        "Dragon Crystal", "GG Aleste II", "Ristar", "Tails Adventure",
        "Aladdin", "Mortal Kombat", "Columns", "Baku Baku",
        "Fantasy Zone", "Wonder Boy", "Gunstar Heroes",
        "Lucky Dime Caper Starring Donald Duck, The", "Pac-Man",
        "Shinobi", "Ecco the Dolphin", "Ax Battler - A Legend of Golden Axe",
        "Vampire - Master of Darkness", "Panzer Dragoon Mini",
        "Sonic Drift 2", "Puyo Puyo Tsuu", "Halley Wars",
    ],
    "pce": [
        "Bonk's Adventure", "Bonk's Revenge", "Blazing Lazers", "R-Type",
        "Ninja Spirit", "Devil's Crush", "Legendary Axe, The",
        "Military Madness", "Neutopia", "Neutopia II", "Bomberman '94",
        "Splatterhouse", "Dragon Spirit", "Soldier Blade",
        "Super Star Soldier", "Air Zonk", "Alien Crush",
        "New Adventure Island", "Parasol Stars - The Story of Bubble Bobble III",
        "Chase H.Q.", "Cadash", "Galaga '90",
        "Keith Courage in Alpha Zones", "Vigilante",
        "Dungeons & Dragons - Order of the Griffon",
        "Battle Lode Runner", "Salamander", "PC Denjin - Punkic Cyborgs",
        "Bomberman", "Final Blaster",
    ],
    "n64": [
        "Super Mario 64", "Legend of Zelda, The - Ocarina of Time",
        "Legend of Zelda, The - Majora's Mask", "GoldenEye 007",
        "Mario Kart 64", "Banjo-Kazooie", "Banjo-Tooie",
        "Super Smash Bros.", "Perfect Dark", "Star Fox 64",
        "Donkey Kong 64", "Paper Mario", "Conker's Bad Fur Day",
        "Diddy Kong Racing", "F-Zero X", "Wave Race 64 - Kawasaki Jet Ski",
        "Pokemon Snap", "Pokemon Stadium", "Mario Party 2",
        "Mario Party 3", "Yoshi's Story", "Kirby 64 - The Crystal Shards",
        "Mario Tennis", "Mario Golf", "1080 Snowboarding",
        "Turok - Dinosaur Hunter", "Resident Evil 2",
        "Harvest Moon 64", "Excitebike 64",
        "Ogre Battle 64 - Person of Lordly Caliber",
    ],
    "ngpc": [
        "Sonic The Hedgehog - Pocket Adventure",
        "SNK vs. Capcom - The Match of the Millennium",
        "King of Fighters R-2 - Pocket Fighting Series",
        "Metal Slug - 1st Mission", "Metal Slug - 2nd Mission",
        "Samurai Shodown! 2 - Pocket Fighting Series", "Puzzle Bobble Mini",
        "SNK vs. Capcom - Card Fighters' Clash - SNK Version",
        "SNK vs. Capcom - Card Fighters' Clash - Capcom Version",
        "Dark Arms - Beast Buster 1999", "Faselei!", "Biomotor Unitron",
        "Fantastic Night Dreams - Cotton", "Neo Turf Masters", "Pac-Man",
        "SNK Gals' Fighters", "Big Bang Pro Wrestling", "Crush Roller",
        "Baseball Stars Color - Pocket Sports Series", "Magical Drop Pocket",
        "Ganbare Neo Poke-Kun", "NeoGeo Cup '98 Plus Color - Pocket Sports Series",
        "Puyo Pop",
    ],
}


def _release_rank(rom: Rom) -> tuple:
    """Sort key picking the release someone naming a game actually wants.

    Region order is not cosmetic on these systems: SNES and Mega Drive PAL
    carts run at 50 Hz, roughly 17% slower than the NTSC original, so a
    European build is a worse default even when the language is the same.
    """
    if "USA" in rom.region:
        region_rank = 0
    elif "Mundial" in rom.region:
        region_rank = 1
    elif "Europa" in rom.region:
        region_rank = 2
    else:
        region_rank = 3
    return (0 if rom.available else 1, region_rank,
            1 if rom.revision else 0, len(rom.stem))


def popular(console_id: str = "gba", limit: int = 60) -> list[Rom]:
    """A hand-picked "known good" shelf for the empty state."""
    picks = _POPULAR_PICKS.get(console_id, [])
    roms = get_index(console_id)
    by_title: dict[str, list[Rom]] = {}
    for rom in roms:
        by_title.setdefault(_norm(rom.title), []).append(rom)

    out: list[Rom] = []
    for pick in picks:
        matches = by_title.get(_norm(pick))
        if not matches:
            continue
        matches = sorted(matches, key=_release_rank)
        out.append(matches[0])
        if len(out) >= limit:
            break
    return out or _collapse_versions(roms)[:limit]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def local_path(rom: Rom) -> Optional[Path]:
    """The extracted ROM file on disk, if this ROM was already downloaded."""
    console = CONSOLES.get(rom.console_id)
    exts = console.rom_exts if console else (".gba",)
    folder = roms_dir(rom.console_id)
    for ext in exts:
        candidate = folder / f"{rom.stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _extract_zip(archive_path: Path, folder: Path, rom: Rom,
                 console: Console) -> Path:
    with zipfile.ZipFile(archive_path) as zf:
        members = [
            member for member in zf.namelist()
            if any(member.lower().endswith(ext) for ext in console.rom_exts)
        ]
        if not members:
            raise RomCatalogError("El .zip no contiene ninguna ROM reconocible")
        member = members[0]
        target = folder / f"{rom.stem}{Path(member).suffix}"
        with zf.open(member) as src, open(target, "wb") as dst:
            while True:
                block = src.read(1024 * 1024)
                if not block:
                    break
                dst.write(block)
    return target


def _seven_zip_executable() -> Optional[str]:
    for candidate in (
        RESOURCE_DIR / "7z" / "7zr.exe",
        RESOURCE_DIR / "7z" / "7z.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    for command in ("7z", "7zz", "7zr"):
        found = shutil.which(command)
        if found:
            return found
    return None


def _extract_7z(archive_path: Path, folder: Path, rom: Rom,
                console: Console, cancel=None) -> Path:
    executable = _seven_zip_executable()
    if executable is None:
        raise RomCatalogError("No encuentro 7-Zip para descomprimir el juego")
    if cancel is not None and cancel.is_set():
        raise RomCatalogError("Descarga cancelada")

    with tempfile.TemporaryDirectory(prefix=".extract-", dir=folder) as temp_name:
        result = subprocess.run(
            [executable, "e", str(archive_path), f"-o{temp_name}", "-y"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60 * 60,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "error desconocido").strip()
            raise RomCatalogError(f"7-Zip no pudo descomprimir el juego: {detail}")
        if cancel is not None and cancel.is_set():
            raise RomCatalogError("Descarga cancelada")

        candidates = [
            path for path in Path(temp_name).rglob("*")
            if path.is_file()
            and any(path.name.lower().endswith(ext) for ext in console.rom_exts)
        ]
        if not candidates:
            raise RomCatalogError("El .7z no contiene ninguna imagen de disco reconocible")
        # Redump CD releases can carry several BIN tracks. The largest one is
        # the data track PCSX2 needs, while tiny audio tracks are not bootable.
        source = max(candidates, key=lambda path: path.stat().st_size)
        target = folder / f"{rom.stem}{source.suffix.lower()}"
        source.replace(target)
        return target


def download(rom: Rom, progress: Optional[Callable[[float, int, int], None]] = None,
             cancel=None) -> Path:
    """Fetch a ROM and leave the playable file in ``roms/<console>/``.

    Archive items store one container per title (.zip for cartridge systems,
    .7z for PS2). The selected game alone is downloaded, unpacked, and removed.
    ``progress`` is called as (fraction 0..1, bytes_done, bytes_total).
    """
    existing = local_path(rom)
    if existing is not None:
        return existing
    if not rom.available:
        reason = rom.unavailable_reason or "La fuente no permite descargar esta versión"
        raise RomCatalogError(reason)

    console = CONSOLES.get(rom.console_id)
    if console is None:
        raise RomCatalogError(f"Consola desconocida: {rom.console_id}")
    folder = roms_dir(rom.console_id)
    archive_path = folder / rom.filename
    part_path = archive_path.with_suffix(archive_path.suffix + ".part")

    try:
        with requests.get(rom.download_url, headers=_UA, stream=True,
                          timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or rom.size_bytes or 0)
            done = 0
            with open(part_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise RomCatalogError("Descarga cancelada")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress((done / total) if total else 0.0, done, total)
        part_path.replace(archive_path)
    except RomCatalogError:
        part_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part_path.unlink(missing_ok=True)
        raise RomCatalogError(f"Fallo la descarga: {exc}") from exc

    container_ext = _archive_ext(archive_path.name, console)
    if not container_ext:
        return archive_path

    try:
        if container_ext == ".zip":
            target = _extract_zip(archive_path, folder, rom, console)
        elif container_ext == ".7z":
            target = _extract_7z(archive_path, folder, rom, console, cancel=cancel)
        else:
            raise RomCatalogError(f"Formato de archivo no compatible: {container_ext}")
    except RomCatalogError:
        raise
    except Exception as exc:
        raise RomCatalogError(f"No pude descomprimir la ROM: {exc}") from exc
    finally:
        archive_path.unlink(missing_ok=True)

    return target
