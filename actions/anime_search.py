"""Anime metadata via Jikan (https://api.jikan.moe) — the unofficial MAL API.

Jikan wraps MyAnimeList, the largest anime database. It has explicit sfw=true
filtering that reliably separates anime from adult/hentai content — something
TMDB cannot do consistently. No API key required.

Results are returned as Movie objects (reusing the existing dataclass) with
tmdb_id=0 since MAL IDs are not TMDB IDs; AnimeModePanel resolves the IMDb id
via a TMDB title lookup when launching torrents.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from actions.movie_search import Movie, TMDB_IMG_BASE

_BASE = "https://api.jikan.moe/v4"
_TIMEOUT = 10
_LAST_CALL: list[float] = [0.0]  # simple per-process rate-limit guard


class AnimeSearchError(RuntimeError):
    """Raised when the Jikan lookup fails."""


def _http_get(url: str, params: dict | None = None) -> dict:
    # Jikan allows 3 req/s; enforce a 400ms gap between calls.
    gap = time.monotonic() - _LAST_CALL[0]
    if gap < 0.4:
        time.sleep(0.4 - gap)
    _LAST_CALL[0] = time.monotonic()

    try:
        r = requests.get(url, params=params or {}, timeout=_TIMEOUT,
                         headers={"User-Agent": "Jarvis/1.0"})
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise AnimeSearchError(f"Jikan request failed: {exc}") from exc


def _parse(item: dict) -> Optional[Movie]:
    try:
        images = item.get("images", {})
        poster = (
            images.get("jpg", {}).get("large_image_url")
            or images.get("jpg", {}).get("image_url")
            or ""
        )
        title = (
            item.get("title_english")
            or item.get("title")
            or ""
        )
        year = 0
        aired = item.get("aired") or item.get("published") or {}
        prop = aired.get("prop", {}).get("from", {})
        year = int(prop.get("year") or 0)
        rating = float(item.get("score") or 0)
        overview = item.get("synopsis") or ""
        return Movie(
            tmdb_id=0,          # MAL id not stored; title lookup used for torrents
            title=title,
            release_year=year,
            poster_url=poster,
            overview=overview,
            rating=rating,
            media_type="tv",
        )
    except (KeyError, ValueError, TypeError):
        return None


def search_anime(query: str, limit: int = 12) -> list[Movie]:
    """Search MyAnimeList for anime by title (sfw only)."""
    query = query.strip()
    if not query:
        raise AnimeSearchError("Empty search query.")
    data = _http_get(f"{_BASE}/anime", {"q": query, "sfw": "true",
                                         "limit": limit, "type": "tv"})
    results = [m for item in data.get("data", []) if (m := _parse(item))]
    if not results:
        raise AnimeSearchError(f"No anime found for '{query}'.")
    return results


def get_trending_anime(limit: int = 12) -> list[Movie]:
    """Fetch the top anime by popularity from MAL (sfw only)."""
    data = _http_get(f"{_BASE}/top/anime",
                     {"filter": "bypopularity", "sfw": "true",
                      "type": "tv", "limit": limit})
    return [m for item in data.get("data", []) if (m := _parse(item))]


def get_airing_anime(limit: int = 12) -> list[Movie]:
    """Fetch currently airing anime from MAL (sfw only)."""
    data = _http_get(f"{_BASE}/top/anime",
                     {"filter": "airing", "sfw": "true",
                      "type": "tv", "limit": limit})
    return [m for item in data.get("data", []) if (m := _parse(item))]
