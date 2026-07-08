"""Movie/series metadata via the Cinemeta Stremio addon (v3-cinemeta.strem.io).

This replaces TMDB for movie/series discovery. Cinemeta is the official Stremio
catalog addon: search is ranked by title relevance (so "The Furious" returns the
actual film first, not the more popular "Fast & Furious"), and every result
already carries its IMDb id (tt…) — the id Torrentio/Peerflix key on — so there
is no TMDB→IMDb bridge step to fail. See memory: stremio-addon-flow.

Results are returned as Movie objects (reusing the existing dataclass) with
imdb_id populated.

Addon endpoints (verified live):
  Search : /catalog/{movie|series}/top/search=<query>.json
  Catalog: /catalog/{movie|series}/top.json          (trending/popular)
  Meta   : /meta/{movie|series}/<imdb_id>.json        (detail + episodes)
"""
from __future__ import annotations

import re
from urllib.parse import quote

import requests

from actions.doh import enable_for
from actions.movie_search import Movie

# Route Cinemeta through DNS-over-HTTPS in case the ISP blocks strem.io.
enable_for("strem.io")

_BASE = "https://v3-cinemeta.strem.io"
_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis)"}


class CinemetaError(RuntimeError):
    """Raised when the Cinemeta addon lookup fails."""


def _http_get(path: str) -> dict:
    try:
        r = requests.get(f"{_BASE}{path}", timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise CinemetaError(f"Cinemeta request failed: {exc}") from exc
    except ValueError as exc:
        raise CinemetaError(f"Invalid Cinemeta response: {exc}") from exc


def _first_year(release_info) -> int:
    """releaseInfo is like '2001', '2013-', '2004-2005'. Take the start year."""
    if not release_info:
        return 0
    m = re.match(r"(\d{4})", str(release_info))
    return int(m.group(1)) if m else 0


def _parse(item: dict, mtype: str) -> Movie | None:
    imdb = item.get("imdb_id") or item.get("id") or ""
    if not str(imdb).startswith("tt"):
        return None
    try:
        rating = float(item.get("imdbRating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    return Movie(
        tmdb_id=0,
        title=item.get("name") or "",
        release_year=_first_year(item.get("releaseInfo") or item.get("year")),
        poster_url=item.get("poster") or "",
        backdrop_url=item.get("background") or item.get("poster") or "",
        overview=item.get("description") or "",
        rating=rating,
        media_type="tv" if mtype == "series" else "movie",
        imdb_id=str(imdb),
    )


def _parse_metas(data: dict, mtype: str) -> list[Movie]:
    return [m for it in data.get("metas", []) if (m := _parse(it, mtype))]


def search(query: str, kind: str = "multi", limit: int = 15) -> list[Movie]:
    """Search Cinemeta for movies and/or series, ranked by title relevance.

    kind: "movie" | "series"/"tv" | "multi" (both, movies first).
    """
    query = query.strip()
    if not query:
        raise CinemetaError("Empty search query.")
    q = quote(query)

    if kind in ("movie", "series"):
        types = [kind]
    elif kind == "tv":
        types = ["series"]
    else:
        types = ["movie", "series"]

    results: list[Movie] = []
    for mtype in types:
        try:
            data = _http_get(f"/catalog/{mtype}/top/search={q}.json")
            results.extend(_parse_metas(data, mtype))
        except CinemetaError:
            continue  # one type failing shouldn't kill the whole search

    if not results:
        raise CinemetaError(f"No results found for '{query}'.")
    return results[:limit]


def get_trending(kind: str = "movie", limit: int = 15, **_ignored) -> list[Movie]:
    """Fetch the top movies or series catalog.

    Extra keyword args (e.g. a legacy `window=`) are accepted and ignored so
    this is a drop-in for movie_search.get_trending.
    """
    mtype = "series" if kind in ("series", "tv") else "movie"
    data = _http_get(f"/catalog/{mtype}/top.json")
    return _parse_metas(data, mtype)[:limit]


def get_meta(imdb_id: str, kind: str = "movie") -> Movie:
    """Fetch full metadata for one title."""
    mtype = "series" if kind in ("series", "tv") else "movie"
    data = _http_get(f"/meta/{mtype}/{imdb_id}.json")
    meta = data.get("meta")
    if not meta:
        raise CinemetaError(f"No metadata for {imdb_id}.")
    m = _parse(meta, mtype)
    if not m:
        raise CinemetaError(f"Could not parse metadata for {imdb_id}.")
    return m
