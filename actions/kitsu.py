"""Anime metadata via the Anime Kitsu Stremio addon (anime-kitsu.strem.fun).

This replaces the Jikan/MAL metadata source for anime. The key advantage over
Jikan is that Kitsu is a Stremio addon: every result carries a `kitsu:<id>`
stream id (and a cross-mapped `imdb_id`), and episodes are numbered
*absolutely* (One Piece episode 1044 is `kitsu:12:1044`). Torrentio consumes
those ids directly — its torrent index has `kitsuId` + `kitsuEpisode` columns —
so the noisy title-search matching that torlink couldn't get right is done for
us on the addon side. See memory: stremio-addon-flow.

Results are returned as Movie objects (reusing the existing dataclass) with
kitsu_id populated; the panel hands that id to actions.torrentio to fetch
streams.

Addon endpoints (verified live):
  Search : /catalog/anime/kitsu-anime-list/search=<query>.json
  Catalog: /catalog/anime/<catalog_id>.json   (trending/popular/airing/rating)
  Genre  : /catalog/anime/kitsu-anime-popular/genre=<Genre>.json
  Meta   : /meta/anime/kitsu:<id>.json         (includes videos[] = episodes)
"""
from __future__ import annotations

import re

import requests

from actions.doh import enable_for
from actions.movie_search import Movie

# Route Kitsu through DNS-over-HTTPS in case the ISP blocks strem.fun.
enable_for("strem.fun")

_BASE = "https://anime-kitsu.strem.fun"
_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis)"}

# Catalog ids from the addon manifest.
_CATALOG_TRENDING = "kitsu-anime-trending"
_CATALOG_POPULAR = "kitsu-anime-popular"
_CATALOG_AIRING = "kitsu-anime-airing"

# Genre label -> Kitsu genre option (the addon filters by these exact strings).
GENRES = {
    "Acción": "Action", "Aventura": "Adventure", "Comedia": "Comedy",
    "Drama": "Drama", "Fantasía": "Fantasy", "Misterio": "Mystery",
    "Romance": "Romance", "Sci-Fi": "Sci-Fi", "Deportes": "Sports",
    "Terror": "Horror",
}


class KitsuError(RuntimeError):
    """Raised when the Kitsu addon lookup fails."""


def _http_get(path: str) -> dict:
    try:
        r = requests.get(f"{_BASE}{path}", timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise KitsuError(f"Kitsu request failed: {exc}") from exc
    except ValueError as exc:
        raise KitsuError(f"Invalid Kitsu response: {exc}") from exc


def _first_year(release_info) -> int:
    """Kitsu's releaseInfo is like '2004-2005' or '2013-'. Take the start year."""
    if not release_info:
        return 0
    m = re.match(r"(\d{4})", str(release_info))
    return int(m.group(1)) if m else 0


# Kitsu poster URLs come in two shapes:
#   .../poster_images/<id>/medium.jpg              (numeric id, most anime)
#   .../anime/<id>/poster_image/medium-<hash>.jpeg (hashed uploads, no larger
#                                                    variant exists — 404s)
# Only upgrade the first, safe shape; the anchored regex leaves the hashed
# shape untouched so we never point at a URL that doesn't exist.
_POSTER_MEDIUM_RE = re.compile(r"(/poster_images/\d+/)medium(\.jpg)$")


def _upgrade_poster(url: str) -> str:
    """Swap the known-safe 'medium' poster URL shape for 'large' (390x554 ->
    550x780). Used only as a backdrop fallback, where more pixels matter more
    than for the small grid thumbnail. Returns the original url unchanged for
    any other shape."""
    return _POSTER_MEDIUM_RE.sub(r"\1large\2", url) if url else url


def _parse_meta(item: dict) -> Movie | None:
    """Turn a Kitsu catalog/meta entry into a Movie."""
    kitsu_id = str(item.get("kitsu_id") or "")
    if not kitsu_id:
        # The id field looks like "kitsu:10"; fall back to parsing it.
        raw = str(item.get("id") or "")
        m = re.match(r"kitsu:(\d+)", raw)
        if not m:
            return None
        kitsu_id = m.group(1)
    try:
        rating = float(item.get("imdbRating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    return Movie(
        tmdb_id=0,
        title=item.get("name") or "",
        release_year=_first_year(item.get("releaseInfo")),
        poster_url=item.get("poster") or "",
        backdrop_url=item.get("background") or _upgrade_poster(item.get("poster") or ""),
        overview=item.get("description") or "",
        rating=rating,
        media_type="tv",
        mal_id=0,
        total_episodes=len(item.get("videos") or []),
        kitsu_id=kitsu_id,
    )


def _parse_metas(data: dict, limit: int) -> list[Movie]:
    return [m for it in data.get("metas", []) if (m := _parse_meta(it))][:limit]


def search_anime(query: str, limit: int = 15) -> list[Movie]:
    """Search Kitsu for anime by title. Exact-ranked by the addon."""
    query = query.strip()
    if not query:
        raise KitsuError("Empty search query.")
    from urllib.parse import quote
    data = _http_get(f"/catalog/anime/kitsu-anime-list/search={quote(query)}.json")
    results = _parse_metas(data, limit)
    if not results:
        raise KitsuError(f"No anime found for '{query}'.")
    return results


def get_trending_anime(limit: int = 15) -> list[Movie]:
    """Fetch the trending anime catalog."""
    data = _http_get(f"/catalog/anime/{_CATALOG_TRENDING}.json")
    return _parse_metas(data, limit)


def get_airing_anime(limit: int = 15) -> list[Movie]:
    """Fetch the currently-airing anime catalog."""
    data = _http_get(f"/catalog/anime/{_CATALOG_AIRING}.json")
    return _parse_metas(data, limit)


def get_anime_by_genre(genre: str, limit: int = 15) -> list[Movie]:
    """Fetch the most popular anime for one genre.

    `genre` may be a Kitsu genre string ("Action") or a Spanish label from
    GENRES ("Acción").
    """
    kitsu_genre = GENRES.get(genre, genre)
    from urllib.parse import quote
    data = _http_get(
        f"/catalog/anime/{_CATALOG_POPULAR}/genre={quote(kitsu_genre)}.json")
    results = _parse_metas(data, limit)
    if not results:
        raise KitsuError(f"No anime found for genre '{genre}'.")
    return results


def get_meta(kitsu_id: str) -> Movie:
    """Fetch full metadata for one anime (includes episode count)."""
    kitsu_id = str(kitsu_id).replace("kitsu:", "")
    data = _http_get(f"/meta/anime/kitsu:{kitsu_id}.json")
    meta = data.get("meta")
    if not meta:
        raise KitsuError(f"No metadata for kitsu:{kitsu_id}.")
    m = _parse_meta(meta)
    if not m:
        raise KitsuError(f"Could not parse metadata for kitsu:{kitsu_id}.")
    return m


def get_episodes(kitsu_id: str, limit: int = 2000) -> list[dict]:
    """Fetch the episode list for an anime from Kitsu.

    Returns dicts with:
        number     : absolute episode number (Kitsu numbering)
        title      : episode title
        aired      : YYYY-MM-DD (or "")
        overview   : synopsis (or "")
        thumbnail  : episode still URL (or "")
        stream_id  : the id to pass to Torrentio, e.g. "kitsu:12:1044"
        season     : Kitsu season (0 = specials/OVA)
    """
    kitsu_id = str(kitsu_id).replace("kitsu:", "")
    data = _http_get(f"/meta/anime/kitsu:{kitsu_id}.json")
    meta = data.get("meta") or {}
    episodes = []
    for v in (meta.get("videos") or [])[:limit]:
        released = v.get("released") or ""
        episodes.append({
            "number": int(v.get("episode") or 0),
            "title": v.get("title") or f"Episodio {v.get('episode', '?')}",
            "aired": released[:10] if released else "",
            "overview": v.get("overview") or "",
            "thumbnail": v.get("thumbnail") or "",
            "stream_id": v.get("id") or f"kitsu:{kitsu_id}:{v.get('episode', '')}",
            "season": int(v.get("season") or 0),
        })
    return episodes
