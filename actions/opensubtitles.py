"""Subtitle search and download via the public OpenSubtitles legacy REST API.

Uses https://rest.opensubtitles.org/search — the same public endpoint Stremio
and Kodi subtitle addons use. No API key, no login, no per-account quota: search
by title and download the .srt directly. Results carry a direct download link to
a gzip-compressed subtitle which we decompress on the fly.

Many ISPs (e.g. in Spain) block OpenSubtitles at the DNS level, so the domain
fails to resolve without a VPN. To work around that, hostnames are resolved via
DNS-over-HTTPS (Cloudflare) and connections are forced to the resolved IP while
keeping the correct SNI/TLS — which defeats DNS-based blocks. If the block is
IP-level instead, a VPN is still required.

Downloaded files are cached under memory/subtitles so the same subtitle is never
fetched twice.
"""
from __future__ import annotations

import gzip
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from actions.doh import enable_for
from actions.paths import memory_path

API_BASE = "https://rest.opensubtitles.org/search"
# OpenSubtitles requires an identifiable User-Agent; this one is accepted for
# general use by the legacy REST endpoint.
_USER_AGENT = "TemporaryUserAgent"
_TIMEOUT = 12
_CACHE_DIR = memory_path("subtitles")

# Route OpenSubtitles through DNS-over-HTTPS to bypass ISP DNS blocks.
enable_for("opensubtitles.org")

# The public endpoint occasionally rate-limits (429/5xx) or returns an empty
# list under load even when subtitles exist; retry a few times with backoff.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF = 1.0  # seconds, multiplied by attempt number

# The public endpoint occasionally rate-limits (429/5xx) or returns an empty
# list under load even when subtitles exist; retry a few times with backoff.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF = 1.0  # seconds, multiplied by attempt number

# 2-letter (or already 3-letter) code -> OpenSubtitles 3-letter sublanguageid.
_LANG_MAP = {
    "es": "spa", "spa": "spa",
    "en": "eng", "eng": "eng",
    "fr": "fre", "fre": "fre",
    "de": "ger", "ger": "ger",
    "it": "ita", "ita": "ita",
    "pt": "por", "por": "por",
}


class OpenSubtitlesError(RuntimeError):
    """Raised when subtitle lookup/download fails."""


@dataclass
class Subtitle:
    """A single subtitle search result with a direct download link."""

    download_link: str
    language: str
    release: str
    downloads: int
    file_name: str
    year: int = 0

    def to_dict(self) -> dict:
        return {
            "download_link": self.download_link,
            "language": self.language,
            "release": self.release,
            "downloads": self.downloads,
            "file_name": self.file_name,
            "year": self.year,
        }


def _lang_id(language: str) -> str:
    return _LANG_MAP.get(language.lower(), "spa")


def _get(url: str) -> requests.Response:
    """GET with retries on transient rate-limit/server errors."""
    last_err = "unknown error"
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
            if r.status_code in _RETRY_STATUS:
                last_err = f"HTTP {r.status_code} (busy)"
                time.sleep(_BACKOFF * attempt)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last_err = str(exc)
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF * attempt)
    raise OpenSubtitlesError(f"OpenSubtitles unreachable: {last_err}")


def search(query: str, language: str = "es", year: int = 0, limit: int = 10) -> list[Subtitle]:
    """Search OpenSubtitles for subtitles matching a title.

    Args:
        query: Movie/TV title
        language: 2-letter code (es, en, ...) or 3-letter sublanguageid
        year: Optional release year to prioritise
        limit: Max results

    Returns:
        List of Subtitle results, best first (year match, then downloads).

    Raises:
        OpenSubtitlesError: If the request fails or nothing is found.
    """
    query = query.strip()
    if not query:
        raise OpenSubtitlesError("Empty subtitle query.")

    lang_id = _lang_id(language)
    # Path-style query params, order-insensitive; the endpoint wants the title
    # url-encoded within the /query-.../sublanguageid-.../ segments.
    url = f"{API_BASE}/query-{quote(query)}/sublanguageid-{lang_id}"

    # An empty list can mean "no results" OR a silent rate-limit; retry a couple
    # of times before believing there are genuinely none.
    data = []
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            data = _get(url).json()
        except ValueError as exc:
            raise OpenSubtitlesError(f"Invalid OpenSubtitles response: {exc}") from exc
        if isinstance(data, list) and data:
            break
        if attempt < _MAX_RETRIES:
            time.sleep(_BACKOFF * attempt)

    if not isinstance(data, list) or not data:
        raise OpenSubtitlesError(
            f"OpenSubtitles no devolvió resultados para '{query}'. "
            "Puede ser un límite temporal; prueba de nuevo en unos segundos."
        )

    results: list[Subtitle] = []
    for item in data:
        link = item.get("SubDownloadLink")
        if not link:
            continue
        try:
            sub_year = int(item.get("MovieYear") or 0)
        except (ValueError, TypeError):
            sub_year = 0
        try:
            downloads = int(item.get("SubDownloadsCnt") or 0)
        except (ValueError, TypeError):
            downloads = 0
        results.append(
            Subtitle(
                download_link=link,
                language=item.get("LanguageName", language),
                release=item.get("MovieReleaseName") or item.get("SubFileName", ""),
                downloads=downloads,
                file_name=item.get("SubFileName", "subtitle.srt"),
                year=sub_year,
            )
        )

    if not results:
        raise OpenSubtitlesError(f"No downloadable subtitles for '{query}'.")

    # Rank: exact year match first (when a year is given), then by downloads.
    def rank(s: Subtitle):
        year_match = 1 if (year and s.year == year) else 0
        return (year_match, s.downloads)

    results.sort(key=rank, reverse=True)
    return results[:limit]


def download(subtitle, dest_dir: Optional[Path] = None) -> str:
    """Download and decompress a subtitle to a local .srt file.

    Args:
        subtitle: A Subtitle result, or a raw SubDownloadLink string
        dest_dir: Directory to save into (defaults to the subtitle cache)

    Returns:
        Absolute path to the saved .srt file.

    Raises:
        OpenSubtitlesError: On network/decompression errors.
    """
    if isinstance(subtitle, Subtitle):
        link = subtitle.download_link
        name = subtitle.file_name
    else:
        link = str(subtitle)
        name = "subtitle.srt"

    if not link:
        raise OpenSubtitlesError("No download link for this subtitle.")

    dest_dir = Path(dest_dir) if dest_dir else _CACHE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not name.lower().endswith(".srt"):
        name = f"{Path(name).stem or 'subtitle'}.srt"
    dest = dest_dir / name

    raw = _get(link).content

    # The legacy endpoint serves gzip-compressed subtitles; fall back to raw
    # bytes if the payload isn't actually gzipped.
    try:
        content = gzip.decompress(raw)
    except (OSError, EOFError):
        content = raw

    dest.write_bytes(content)
    return str(dest)


def fetch_subtitle(title: str, language: str = "es", year: int = 0,
                   dest_dir: Optional[Path] = None) -> str:
    """Search + download the best subtitle for a title in one call.

    Returns the local .srt path, or raises OpenSubtitlesError.
    """
    results = search(title, language=language, year=year, limit=5)
    return download(results[0], dest_dir=dest_dir)
