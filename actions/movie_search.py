"""Movie metadata search via IMDb API (https://imdbapi.dev/).

Free, no-auth API for movie/TV show information: titles, posters, synopses,
ratings, release dates, etc. No API key required.

This module does NOT handle downloading or streaming — only metadata discovery.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import requests

_IMDB_API_BASE = "https://imdbapi.dev/api"
_TIMEOUT = 15


class MovieSearchError(RuntimeError):
    """Raised when IMDb lookup fails (network, no results)."""


@dataclass
class Movie:
    """Minimal movie metadata from IMDb."""

    imdb_id: str
    title: str
    release_year: int = 0
    poster_url: str = ""
    overview: str = ""
    rating: float = 0.0  # IMDb-style 0-10
    media_type: str = "movie"  # "movie" | "tv"

    def to_dict(self) -> dict:
        return asdict(self)


def _http_get(url: str, params: dict | None = None) -> dict:
    """GET to IMDb API, return JSON. Raise MovieSearchError on failure."""
    try:
        r = requests.get(url, params=params or {}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise MovieSearchError(
            f"IMDb API request failed ({exc.__class__.__name__}). "
            "The service may be unavailable. Try again later."
        ) from exc


def _parse_movie(data: dict, media_type: str = "movie") -> Optional[Movie]:
    """Parse an IMDb result dict into a Movie object."""
    try:
        imdb_id = data.get("#IMDB_ID") or data.get("id", "")
        title = data.get("#TITLE") or ""
        year_str = data.get("#YEAR", "0")
        poster = data.get("#IMG_POSTER") or ""
        rating_str = data.get("#IMDB_IV") or "0"

        # Parse rating (IMDb returns as string like "8.8")
        try:
            rating = float(str(rating_str).split(",")[0]) if rating_str else 0.0
        except (ValueError, IndexError):
            rating = 0.0

        return Movie(
            imdb_id=imdb_id,
            title=title,
            release_year=int(year_str) if year_str.isdigit() else 0,
            poster_url=poster,
            overview=data.get("description", ""),
            rating=rating,
            media_type=media_type,
        )
    except (KeyError, ValueError, TypeError):
        return None


def search(query: str, kind: str = "movie", limit: int = 10) -> list[Movie]:
    """Search IMDb for movies/TV shows.

    Args:
        query: Title or text to search for
        kind: "movie" | "tv" (IMDb API treats all the same, we just tag)
        limit: Max results

    Returns:
        List of Movie objects

    Raises:
        MovieSearchError: If request fails or no results.
    """
    query = query.strip()
    if not query:
        raise MovieSearchError("Empty search query.")

    url = f"{_IMDB_API_BASE}/search"
    data = _http_get(url, {"q": query})

    results: list[Movie] = []
    for item in data.get("description", [])[:limit]:
        movie = _parse_movie(item, kind)
        if movie and movie.title and movie.imdb_id:
            results.append(movie)

    if not results:
        raise MovieSearchError(f"No results found for '{query}'.")
    return results


def get_trending(kind: str = "movie", limit: int = 20) -> list[Movie]:
    """Fetch popular movies (mock with search of popular titles).

    Note: IMDb API doesn't have a dedicated trending endpoint, so we search
    for perennially popular titles as a fallback.
    """
    popular_queries = [
        "The Shawshank Redemption",
        "The Dark Knight",
        "Inception",
        "Pulp Fiction",
        "Forrest Gump",
        "Fight Club",
        "The Matrix",
        "Interstellar",
        "The Godfather",
        "The Avengers",
    ]

    results: list[Movie] = []
    for query in popular_queries[:limit // 2]:
        try:
            found = search(query, kind=kind, limit=1)
            if found:
                results.append(found[0])
        except MovieSearchError:
            continue
        if len(results) >= limit:
            break
    return results[:limit]


def get_details(imdb_id: str) -> Optional[Movie]:
    """Fetch full details for a movie/TV show by IMDb ID."""
    url = f"{_IMDB_API_BASE}/title/{imdb_id}"
    try:
        data = _http_get(url)
        return _parse_movie(data)
    except MovieSearchError:
        return None


def search_action(parameters: dict) -> str:
    """Voice/agent entry point for movie search.

    parameters:
        query: title to search
        kind: "movie" | "tv" (default "movie")
    """
    query = (parameters.get("query") or "").strip()
    kind = (parameters.get("kind") or "movie").strip().lower()

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
