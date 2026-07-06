"""Subtitle search and download via the OpenSubtitles REST API.

Looks up subtitles for a movie/TV title, downloads the best match as an .srt
file, and returns the local path so a player (VLC) can load it.

Requires a free API key from https://www.opensubtitles.com/consumers stored in
config/api_keys.json under "opensubtitles_api_key". Searching needs only the
key; downloading is rate-limited per key (generous for personal use).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from actions.paths import config_path, memory_path

API_BASE = "https://api.opensubtitles.com/api/v1"
_USER_AGENT = "Mark-XXXIX v1.0"
_TIMEOUT = 10
_CACHE_DIR = memory_path("subtitles")


class OpenSubtitlesError(RuntimeError):
    """Raised when subtitle lookup/download fails."""


@dataclass
class Subtitle:
    """A single subtitle search result."""

    file_id: int
    language: str
    release: str
    downloads: int
    file_name: str

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "language": self.language,
            "release": self.release,
            "downloads": self.downloads,
            "file_name": self.file_name,
        }


def _get_api_key() -> str:
    """Load the OpenSubtitles API key from config."""
    try:
        cfg = config_path("api_keys.json")
        if cfg.exists():
            with open(cfg, "r", encoding="utf-8") as f:
                return json.load(f).get("opensubtitles_api_key", "")
    except Exception:
        pass
    return ""


def _headers(api_key: str) -> dict:
    return {
        "Api-Key": api_key,
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def search(query: str, language: str = "es", year: int = 0, limit: int = 10) -> list[Subtitle]:
    """Search OpenSubtitles for subtitles matching a title.

    Args:
        query: Movie/TV title
        language: 2-letter language code (default Spanish)
        year: Optional release year to narrow results
        limit: Max results

    Returns:
        List of Subtitle results, best (most-downloaded) first.

    Raises:
        OpenSubtitlesError: If the key is missing, the request fails, or nothing
            is found.
    """
    query = query.strip()
    if not query:
        raise OpenSubtitlesError("Empty subtitle query.")

    api_key = _get_api_key()
    if not api_key:
        raise OpenSubtitlesError(
            "OpenSubtitles API key not configured. Add opensubtitles_api_key to "
            "config/api_keys.json (free from https://www.opensubtitles.com/consumers)."
        )

    params = {"query": query, "languages": language}
    if year:
        params["year"] = str(year)

    try:
        r = requests.get(
            f"{API_BASE}/subtitles",
            params=params,
            headers=_headers(api_key),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise OpenSubtitlesError(f"OpenSubtitles search failed: {exc}") from exc
    except ValueError as exc:
        raise OpenSubtitlesError(f"Invalid OpenSubtitles response: {exc}") from exc

    results: list[Subtitle] = []
    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        files = attrs.get("files", [])
        if not files:
            continue
        file_id = files[0].get("file_id")
        if not file_id:
            continue
        results.append(
            Subtitle(
                file_id=int(file_id),
                language=attrs.get("language", language),
                release=attrs.get("release", ""),
                downloads=int(attrs.get("download_count", 0) or 0),
                file_name=files[0].get("file_name", "subtitle.srt"),
            )
        )

    if not results:
        raise OpenSubtitlesError(f"No subtitles found for '{query}'.")

    results.sort(key=lambda s: s.downloads, reverse=True)
    return results[:limit]


def download(file_id: int, dest_dir: Optional[Path] = None) -> str:
    """Download a subtitle file by its file_id.

    Args:
        file_id: The OpenSubtitles file id (from a Subtitle result)
        dest_dir: Directory to save into (defaults to the subtitle cache)

    Returns:
        Absolute path to the saved .srt file.

    Raises:
        OpenSubtitlesError: On auth/network/quota errors.
    """
    api_key = _get_api_key()
    if not api_key:
        raise OpenSubtitlesError("OpenSubtitles API key not configured.")

    dest_dir = Path(dest_dir) if dest_dir else _CACHE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: request a temporary download link for the file.
    try:
        r = requests.post(
            f"{API_BASE}/download",
            headers=_headers(api_key),
            json={"file_id": int(file_id)},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        info = r.json()
    except requests.RequestException as exc:
        raise OpenSubtitlesError(f"Subtitle download request failed: {exc}") from exc
    except ValueError as exc:
        raise OpenSubtitlesError(f"Invalid download response: {exc}") from exc

    link = info.get("link")
    if not link:
        raise OpenSubtitlesError(
            info.get("message") or "OpenSubtitles did not return a download link (quota reached?)."
        )

    file_name = info.get("file_name") or f"{file_id}.srt"
    dest = dest_dir / file_name

    # Step 2: fetch the actual subtitle content.
    try:
        sub = requests.get(link, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
        sub.raise_for_status()
        dest.write_bytes(sub.content)
    except requests.RequestException as exc:
        raise OpenSubtitlesError(f"Subtitle file download failed: {exc}") from exc

    return str(dest)


def fetch_subtitle(title: str, language: str = "es", year: int = 0,
                   dest_dir: Optional[Path] = None) -> str:
    """Search + download the best subtitle for a title in one call.

    Returns the local .srt path, or raises OpenSubtitlesError.
    """
    results = search(title, language=language, year=year, limit=5)
    return download(results[0].file_id, dest_dir=dest_dir)
