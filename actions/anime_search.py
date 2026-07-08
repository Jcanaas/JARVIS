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

_PREFS_KEY = "anime_sfw"


def get_sfw() -> bool:
    """Return True if adult/hentai content should be hidden (default: True)."""
    try:
        import json
        from actions.paths import config_path
        p = config_path("prefs.json")
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get(_PREFS_KEY, True)
    except Exception:
        pass
    return True


def set_sfw(value: bool) -> None:
    """Persist the SFW preference."""
    try:
        import json
        from actions.paths import config_path
        p = config_path("prefs.json")
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        data[_PREFS_KEY] = value
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class AnimeSearchError(RuntimeError):
    """Raised when the Jikan lookup fails."""


def _http_get(url: str, params: dict | None = None) -> dict:
    # Jikan allows 3 req/s; enforce a 400ms gap between calls.
    # Jikan also throws transient 429/5xx routinely, so retry once before
    # letting callers fall back to the CDN-cached endpoints.
    last_exc: Exception | None = None
    last_status = 0
    for attempt in range(2):
        gap = time.monotonic() - _LAST_CALL[0]
        if gap < 0.4:
            time.sleep(0.4 - gap)
        _LAST_CALL[0] = time.monotonic()

        try:
            # Pin Accept-Encoding: with brotli installed, requests advertises
            # br/zstd, and Jikan's CDN misses cache on those variants (504)
            # while serving the gzip/identity ones fine.
            r = requests.get(url, params=params or {}, timeout=_TIMEOUT,
                             headers={"User-Agent": "Jarvis/1.0",
                                      "Accept-Encoding": "gzip, deflate"})
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            last_exc = exc
            last_status = getattr(exc.response, "status_code", 0) if isinstance(
                exc, requests.HTTPError) else 0
            transient = last_status in (429, 500, 502, 503, 504) or not last_status
            if not transient or attempt == 1:
                break
            time.sleep(1.0)
    if last_status in (429, 500, 502, 503, 504):
        raise AnimeSearchError(
            "MyAnimeList (Jikan) no responde ahora mismo — "
            "inténtalo de nuevo en unos minutos."
        ) from last_exc
    raise AnimeSearchError(f"Jikan request failed: {last_exc}") from last_exc


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
        backdrop = (
            images.get("jpg", {}).get("large_image_url")
            or images.get("webp", {}).get("large_image_url")
            or ""
        )
        return Movie(
            tmdb_id=0,
            title=title,
            release_year=year,
            poster_url=poster,
            backdrop_url=backdrop,
            overview=overview,
            rating=rating,
            media_type="tv",
            mal_id=int(item.get("mal_id") or 0),
            total_episodes=int(item.get("episodes") or 0),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _sfw_filter_raw(items: list[dict]) -> list[dict]:
    """Client-side SFW filter for fallback queries that lack the sfw param."""
    if not get_sfw():
        return items
    out = []
    for it in items:
        rating = it.get("rating") or ""
        gids = {g.get("mal_id") for g in
                (it.get("genres") or []) + (it.get("explicit_genres") or [])}
        if rating.startswith("Rx") or 12 in gids:  # 12 = Hentai
            continue
        out.append(it)
    return out


def _fallback_cached(endpoint: str, limit: int,
                     genre_id: int | None = None) -> list[Movie]:
    """Degraded mode: when Jikan's backend is down it still serves its
    parameterless, CDN-cached queries — fetch one and filter client-side."""
    data = _http_get(f"{_BASE}/{endpoint}")
    raw = data.get("data", [])
    if genre_id is not None:
        raw = [it for it in raw if any(
            g.get("mal_id") == genre_id for g in
            (it.get("genres") or []) + (it.get("themes") or [])
            + (it.get("demographics") or [])
        )]
    raw = _sfw_filter_raw(raw)
    return [m for item in raw[:limit] if (m := _parse(item))]


def search_anime(query: str, limit: int = 12) -> list[Movie]:
    """Search MyAnimeList for anime by title."""
    query = query.strip()
    if not query:
        raise AnimeSearchError("Empty search query.")
    params: dict = {"q": query, "limit": limit, "type": "tv"}
    if get_sfw():
        params["sfw"] = "true"
    data = _http_get(f"{_BASE}/anime", params)
    results = [m for item in data.get("data", []) if (m := _parse(item))]
    if not results:
        raise AnimeSearchError(f"No anime found for '{query}'.")
    return results


def get_trending_anime(limit: int = 12) -> list[Movie]:
    """Fetch the top anime by popularity from MAL."""
    params: dict = {"filter": "bypopularity", "type": "tv", "limit": limit}
    if get_sfw():
        params["sfw"] = "true"
    try:
        data = _http_get(f"{_BASE}/top/anime", params)
    except AnimeSearchError:
        return _fallback_cached("top/anime", limit)
    return [m for item in data.get("data", []) if (m := _parse(item))]


def get_airing_anime(limit: int = 12) -> list[Movie]:
    """Fetch currently airing anime from MAL."""
    params: dict = {"filter": "airing", "type": "tv", "limit": limit}
    if get_sfw():
        params["sfw"] = "true"
    try:
        data = _http_get(f"{_BASE}/top/anime", params)
    except AnimeSearchError:
        return _fallback_cached("seasons/now", limit)
    return [m for item in data.get("data", []) if (m := _parse(item))]


# MAL genre ids used by the genre pill row in AnimeModePanel.
GENRES = {
    "Acción": 1, "Aventura": 2, "Comedia": 4, "Drama": 8, "Fantasía": 10,
    "Misterio": 7, "Romance": 22, "Sci-Fi": 24, "Deportes": 30, "Terror": 14,
}


def get_anime_by_genre(genre_id: int, limit: int = 15) -> list[Movie]:
    """Fetch the most popular anime for one MAL genre."""
    params: dict = {
        "genres": genre_id, "type": "tv", "limit": limit,
        "order_by": "members", "sort": "desc",
    }
    if get_sfw():
        params["sfw"] = "true"
    try:
        data = _http_get(f"{_BASE}/anime", params)
    except AnimeSearchError:
        results = _fallback_cached("top/anime", limit, genre_id=genre_id)
        if not results:
            raise
        return results
    return [m for item in data.get("data", []) if (m := _parse(item))]


def get_episodes(mal_id: int, limit: int = 100) -> list[dict]:
    """Fetch episode list for an anime from Jikan.

    Returns list of dicts: number, title, aired, filler, recap.
    Jikan paginates at 100 eps/page; fetches only page 1.
    """
    data = _http_get(f"{_BASE}/anime/{mal_id}/episodes", {"page": 1})
    episodes = []
    for ep in data.get("data", [])[:limit]:
        aired_raw = ep.get("aired") or ""
        episodes.append({
            "number": int(ep.get("mal_id") or 0),
            "title": (
                ep.get("title")
                or ep.get("title_romanji")
                or f"Episodio {ep.get('mal_id', '?')}"
            ),
            "aired": aired_raw[:10] if aired_raw else "",
            "filler": bool(ep.get("filler")),
            "recap": bool(ep.get("recap")),
        })
    return episodes
