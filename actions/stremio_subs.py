"""Subtitles via Stremio subtitle addons (OpenSubtitles v3 backend).

Replaces the legacy rest.opensubtitles.org REST endpoint (title-based search,
gzip payloads, unreliable rate-limiting) with the same by-id pattern used for
metadata/torrents elsewhere in this app: query an addon with the id we already
have on the Movie object, get back ready-to-download subtitles.

Two addon hosts, same response shape (both proxy the OpenSubtitles v3 backend
— subs5.strem.io — so results and format are identical):
  Movies/series (IMDb id) : opensubtitles-v3.strem.io/subtitles/{type}/<id[:s:e]>.json
  Anime (Kitsu id)         : anime-kitsu.strem.fun/subtitles/series/kitsu:<id>:<ep>.json

Subtitles are plain UTF-8 .srt served directly by subs5.strem.io — no gzip,
no legacy REST quirks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from actions.doh import enable_for
from actions.paths import memory_path

_OS_BASE = "https://opensubtitles-v3.strem.io"
_KITSU_BASE = "https://anime-kitsu.strem.fun"
_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis)"}
_CACHE_DIR = memory_path("subtitles")

# Both addon hosts and their shared subtitle-file backend.
enable_for("strem.io", "strem.fun")

# 2-letter (or already 3-letter) code -> OpenSubtitles 3-letter lang code.
_LANG_MAP = {
    "es": "spa", "spa": "spa",
    "en": "eng", "eng": "eng",
    "fr": "fre", "fre": "fre",
    "de": "ger", "ger": "ger",
    "it": "ita", "ita": "ita",
    "pt": "por", "por": "por",
}


class StremioSubsError(RuntimeError):
    """Raised when the subtitle addon lookup/download fails."""


@dataclass
class Subtitle:
    """A single subtitle result with a direct download link."""

    sub_id: str
    url: str
    lang: str  # 3-letter OpenSubtitles code (spa, eng, ...)
    encoding: str = ""

    def to_dict(self) -> dict:
        return {"id": self.sub_id, "url": self.url, "lang": self.lang,
                "encoding": self.encoding}


def _lang_code(language: str) -> str:
    return _LANG_MAP.get(language.lower(), "spa")


def _get_json(url: str) -> dict:
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise StremioSubsError(f"Subtitle addon request failed: {exc}") from exc
    except ValueError as exc:
        raise StremioSubsError(f"Invalid subtitle addon response: {exc}") from exc


def _parse(data: dict, language: str, limit: int) -> list[Subtitle]:
    code = _lang_code(language)
    out = []
    for item in data.get("subtitles", []):
        if item.get("lang") != code:
            continue
        url = item.get("url")
        if not url:
            continue
        out.append(Subtitle(
            sub_id=str(item.get("id", "")),
            url=url,
            lang=item.get("lang", code),
            encoding=item.get("SubEncoding", ""),
        ))
    if not out:
        raise StremioSubsError(f"No hay subtítulos en '{language}'.")
    return out[:limit]


def search_by_imdb(imdb_id: str, kind: str = "movie", season: int = 0,
                   episode: int = 0, language: str = "es",
                   limit: int = 5) -> list[Subtitle]:
    """Search subtitles for a movie/series by IMDb id via the OS-v3 addon."""
    if not imdb_id:
        raise StremioSubsError("No IMDb id provided.")
    stype = "series" if kind in ("series", "tv") else "movie"
    video_id = imdb_id
    if stype == "series" and season and episode:
        video_id = f"{imdb_id}:{season}:{episode}"
    data = _get_json(f"{_OS_BASE}/subtitles/{stype}/{video_id}.json")
    return _parse(data, language, limit)


def search_by_kitsu(kitsu_id: str, episode: int = 0, language: str = "es",
                    limit: int = 5) -> list[Subtitle]:
    """Search subtitles for an anime episode by Kitsu id via the Kitsu addon."""
    kitsu_id = str(kitsu_id).replace("kitsu:", "")
    if not kitsu_id:
        raise StremioSubsError("No Kitsu id provided.")
    video_id = f"kitsu:{kitsu_id}:{episode}" if episode else f"kitsu:{kitsu_id}"
    data = _get_json(f"{_KITSU_BASE}/subtitles/series/{video_id}.json")
    return _parse(data, language, limit)


def download(subtitle: Subtitle, dest_dir: Optional[Path] = None) -> str:
    """Download a subtitle to a local .srt file.

    Returns the absolute path. Raises StremioSubsError on network failure.
    """
    if not subtitle.url:
        raise StremioSubsError("No download link for this subtitle.")

    dest_dir = Path(dest_dir) if dest_dir else _CACHE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{subtitle.sub_id or 'subtitle'}.srt"

    try:
        r = requests.get(subtitle.url, timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        raw = r.content
    except requests.RequestException as exc:
        raise StremioSubsError(f"Subtitle download failed: {exc}") from exc

    # subs5.strem.io serves plain UTF-8 .srt directly, but fall back to gzip
    # decompression defensively in case a CDN variant compresses the payload.
    import gzip
    try:
        content = gzip.decompress(raw)
    except (OSError, EOFError):
        content = raw

    dest.write_bytes(content)
    return str(dest)


def fetch_subtitle_by_imdb(imdb_id: str, kind: str = "movie", season: int = 0,
                           episode: int = 0, language: str = "es",
                           dest_dir: Optional[Path] = None) -> str:
    """Search + download the first matching subtitle for an IMDb id."""
    results = search_by_imdb(imdb_id, kind=kind, season=season,
                             episode=episode, language=language, limit=1)
    return download(results[0], dest_dir=dest_dir)


def fetch_subtitle_by_kitsu(kitsu_id: str, episode: int = 0,
                            language: str = "es",
                            dest_dir: Optional[Path] = None) -> str:
    """Search + download the first matching subtitle for a Kitsu episode."""
    results = search_by_kitsu(kitsu_id, episode=episode, language=language,
                              limit=1)
    return download(results[0], dest_dir=dest_dir)
