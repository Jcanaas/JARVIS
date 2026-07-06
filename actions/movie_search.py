"""Movie metadata search via TMDB (The Movie Database).

Uses the free TMDB API to fetch movie/TV show information: titles, posters,
synopses, ratings, release dates, etc. No authentication required beyond an API
key (free tier from https://www.themoviedb.org/settings/api).

This module does NOT handle downloading or streaming — only metadata discovery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests

from actions.paths import config_path, memory_path

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w342"  # 342px width (mobile-friendly)
_TIMEOUT = 5  # TMDB is fast, 5s is plenty
_CACHE_DIR = memory_path("tmdb_cache")


class MovieSearchError(RuntimeError):
    """Raised when TMDB lookup fails (network, auth, no results)."""


@dataclass
class Movie:
    """Minimal movie metadata from TMDB."""

    tmdb_id: int
    title: str
    release_year: int = 0
    poster_url: str = ""
    overview: str = ""
    rating: float = 0.0  # IMDb-style 0-10
    media_type: str = "movie"  # "movie" | "tv"

    def to_dict(self) -> dict:
        return asdict(self)


def _get_api_key() -> str:
    """Load TMDB API key from config. Falls back to empty if not configured."""
    try:
        cfg = config_path("api_keys.json")
        if cfg.exists():
            with open(cfg, "r", encoding="utf-8") as f:
                return json.load(f).get("tmdb_api_key", "")
    except Exception:
        pass
    return ""


def _http_get(url: str, params: dict | None = None) -> dict:
    """GET to TMDB API, return JSON. Raise MovieSearchError on failure."""
    try:
        r = requests.get(url, params=params or {}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise MovieSearchError(
            f"TMDB request failed ({exc.__class__.__name__}). "
            "Check your internet connection or API key."
        ) from exc


def _parse_movie(data: dict, media_type: str = "movie") -> Optional[Movie]:
    """Parse a TMDB result dict into a Movie object."""
    try:
        return Movie(
            tmdb_id=data.get("id", 0),
            title=data.get("title") or data.get("name", ""),
            release_year=int(
                (data.get("release_date") or data.get("first_air_date") or "")[:4] or 0
            ),
            poster_url=(
                f"{TMDB_IMG_BASE}{data['poster_path']}"
                if data.get("poster_path")
                else ""
            ),
            overview=data.get("overview", ""),
            rating=float(data.get("vote_average", 0) or 0),
            media_type=media_type,
        )
    except (KeyError, ValueError, TypeError):
        return None


def search(query: str, kind: str = "multi", limit: int = 10) -> list[Movie]:
    """Search TMDB for movies/TV shows.

    Args:
        query: Title or text to search for
        kind: "movie" | "tv" | "multi" (default: both)
        limit: Max results (TMDB returns up to 20 per page)

    Returns:
        List of Movie objects (or TV objects with media_type="tv")

    Raises:
        MovieSearchError: If API key missing, request fails, or no results.
    """
    api_key = _get_api_key()
    if not api_key:
        raise MovieSearchError(
            "TMDB API key not configured. "
            "Add tmdb_api_key to config/api_keys.json (free from https://www.themoviedb.org)"
        )

    query = query.strip()
    if not query:
        raise MovieSearchError("Empty search query.")

    url = f"{TMDB_API_BASE}/search/{kind}"
    data = _http_get(
        url,
        {
            "api_key": api_key,
            "query": query,
            "language": "es-ES",  # Spanish results preferred
            "page": 1,
        },
    )

    results: list[Movie] = []
    for item in data.get("results", [])[:limit]:
        media_type = item.get("media_type", kind)
        movie = _parse_movie(item, media_type)
        if movie and movie.title:
            results.append(movie)
    if not results:
        raise MovieSearchError(f"No results found for '{query}'.")
    return results


def get_trending(kind: str = "movie", window: str = "week", limit: int = 20) -> list[Movie]:
    """Fetch trending movies or TV shows.

    Args:
        kind: "movie" | "tv"
        window: "day" | "week"
        limit: Max results

    Returns:
        List of Movie objects
    """
    api_key = _get_api_key()
    if not api_key:
        raise MovieSearchError("TMDB API key not configured.")

    url = f"{TMDB_API_BASE}/trending/{kind}/{window}"
    data = _http_get(url, {"api_key": api_key, "language": "es-ES"})

    results: list[Movie] = []
    for item in data.get("results", [])[:limit]:
        movie = _parse_movie(item, kind)
        if movie and movie.title:
            results.append(movie)
    return results


def get_details(tmdb_id: int, kind: str = "movie") -> Optional[Movie]:
    """Fetch full details for a movie/TV show by TMDB ID."""
    api_key = _get_api_key()
    if not api_key:
        raise MovieSearchError("TMDB API key not configured.")

    url = f"{TMDB_API_BASE}/{kind}/{tmdb_id}"
    data = _http_get(url, {"api_key": api_key, "language": "es-ES"})
    return _parse_movie(data, kind)


def search_action(parameters: dict) -> str:
    """Voice/agent entry point for movie search.

    parameters:
        query: title to search
        kind: "movie" | "tv" | "multi" (default "multi")
    """
    query = (parameters.get("query") or "").strip()
    kind = (parameters.get("kind") or "multi").strip().lower()

    if not query:
        return "¿Qué película o serie buscas?"

    try:
        results = search(query, kind=kind, limit=8)
        lines = [f"Encontré {len(results)} resultados:"]
        for i, m in enumerate(results, 1):
            year_str = f" ({m.release_year})" if m.release_year else ""
            rating = f" ★{m.rating:.1f}" if m.rating else ""
            lines.append(f"{i}. {m.title}{year_str}{rating}")
        return "\n".join(lines)
    except MovieSearchError as exc:
        return f"Error buscando películas: {exc}"
