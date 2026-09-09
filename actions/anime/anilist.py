"""Anime catalog via the AniList GraphQL API.

The primary catalog source, with the Kitsu Stremio addon (actions.anime.
catalog) as the fallback. The reason is availability, not features: the Kitsu
addon and Torrentio are *both* served from strem.fun, so a single host outage
took out browsing and playback together — observed live during development,
with the whole domain unreachable for a stretch while AniList, ani.zip,
AnimeTosho and Nyaa all stayed up. Splitting the catalog onto a different,
larger provider removes that shared failure.

This is also what Shiru does: its home, search and sections all call AniList
directly (`sections.js` -> `anilistClient.search`), and MyAnimeList is only
consulted for a signed-in user's own lists. Shiru has no catalog fallback at
all; keeping Kitsu behind AniList makes ours strictly more resilient.

**The identity caveat.** Everything downstream here — episode lists, torrent
lookup, watch progress — is keyed by Kitsu id, so AniList results are
translated via api.ani.zip before they leave this module. That mapping does
not exist for very new shows: measured 2026-09-04, 8 of 10 trending ids
resolved, and both misses were currently-airing 2026 premieres. Those come
back with an empty kitsu_id rather than being hidden, and
`resolve_kitsu_id()` gets a second chance by title against Kitsu itself.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

from actions.anime import cache
from actions.doh import enable_for
from actions.movie_search import Movie

enable_for("anilist.co")

_URL = "https://graphql.anilist.co"
_TIMEOUT = 12
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

# Only the fields we actually map onto Movie — AniList bills by query
# complexity, and a lean selection is also a smaller thing to break.
_MEDIA_FIELDS = """
    id
    idMal
    title { romaji english }
    description(asHtml: false)
    episodes
    seasonYear
    averageScore
    coverImage { extraLarge large }
    bannerImage
    format
"""

_SEARCH_QUERY = """
query ($page: Int, $perPage: Int, $search: String, $sort: [MediaSort], $genre: String, $status: MediaStatus) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, isAdult: false, search: $search, sort: $sort, genre: $genre, status: $status) {
      %s
    }
  }
}
""" % _MEDIA_FIELDS


class AniListError(RuntimeError):
    """Raised when the AniList lookup fails."""


def _post(variables: dict) -> dict:
    try:
        r = requests.post(_URL, json={"query": _SEARCH_QUERY, "variables": variables},
                          timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise AniListError(f"AniList request failed: {exc}") from exc
    except ValueError as exc:
        raise AniListError(f"Invalid AniList response: {exc}") from exc
    if data.get("errors"):
        raise AniListError(f"AniList error: {data['errors']}")
    return data


_TAG_RE = None


def _strip_html(text: str) -> str:
    """AniList descriptions carry <br>/<i> even with asHtml:false."""
    global _TAG_RE
    if _TAG_RE is None:
        import re
        _TAG_RE = re.compile(r"<[^>]+>")
    return _TAG_RE.sub("", text or "").replace("&nbsp;", " ").strip()


def _parse_media(item: dict) -> Optional[Movie]:
    anilist_id = item.get("id")
    if not anilist_id:
        return None
    titles = item.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or ""
    if not title:
        return None
    score = item.get("averageScore")
    cover = item.get("coverImage") or {}
    return Movie(
        tmdb_id=0,
        title=title,
        release_year=int(item.get("seasonYear") or 0),
        poster_url=cover.get("extraLarge") or cover.get("large") or "",
        backdrop_url=item.get("bannerImage") or cover.get("extraLarge") or "",
        overview=_strip_html(item.get("description")),
        # AniList scores are 0-100; the rest of the app shows IMDb-style 0-10.
        rating=(float(score) / 10.0) if score else 0.0,
        media_type="tv",
        mal_id=int(item.get("idMal") or 0),
        total_episodes=int(item.get("episodes") or 0),
        kitsu_id="",   # filled in by _attach_kitsu_ids
    )


def _attach_kitsu_ids(pairs: list[tuple[Movie, int]]) -> list[Movie]:
    """Resolve each AniList id to the Kitsu id the rest of the app keys on.

    Takes (movie, anilist_id) pairs rather than reading the ids from shared
    state: this runs inside a Flask request handler, so anything module-level
    would be clobbered by a concurrent request.

    Done in parallel because it is one api.ani.zip call per title; those are
    cached for 30 days, so it is only slow on a genuinely cold grid
    (measured: 0.47s for 10 titles cold, 0.03s warm). A title with no mapping
    keeps an empty kitsu_id rather than being dropped — see the module
    docstring on new shows.
    """
    if not pairs:
        return []
    from actions.anime import mappings

    def resolve(pair: tuple[Movie, int]) -> Movie:
        movie, anilist_id = pair
        try:
            movie.kitsu_id = mappings.mappings(anilist_id=anilist_id).kitsu_id
        except Exception:
            movie.kitsu_id = ""
        return movie

    with ThreadPoolExecutor(max_workers=min(10, len(pairs))) as pool:
        return list(pool.map(resolve, pairs))


def _parse_page(data: dict, limit: int) -> list[Movie]:
    raw = ((data.get("data") or {}).get("Page") or {}).get("media") or []
    pairs = []
    for item in raw[:limit]:
        m = _parse_media(item)
        if m:
            pairs.append((m, item["id"]))
    return _attach_kitsu_ids(pairs)


def search_anime(query: str, limit: int = 20) -> list[Movie]:
    query = query.strip()
    if not query:
        raise AniListError("Empty search query.")
    data = cache.cached(
        "anilist_search", query.lower(),
        lambda: _post({"page": 1, "perPage": limit, "search": query,
                       "sort": ["SEARCH_MATCH"]}))
    results = _parse_page(data, limit)
    if not results:
        raise AniListError(f"No anime found for '{query}'.")
    return results


def get_trending_anime(limit: int = 20) -> list[Movie]:
    data = cache.cached(
        "anilist_trending", "trending",
        lambda: _post({"page": 1, "perPage": limit, "sort": ["TRENDING_DESC"]}))
    return _parse_page(data, limit)


def get_airing_anime(limit: int = 20) -> list[Movie]:
    data = cache.cached(
        "anilist_airing", "airing",
        lambda: _post({"page": 1, "perPage": limit, "sort": ["TRENDING_DESC"],
                       "status": "RELEASING"}))
    return _parse_page(data, limit)


# Spanish label -> AniList genre string. AniList genres are a fixed
# vocabulary, so these must match exactly.
GENRES = {
    "Acción": "Action", "Aventura": "Adventure", "Comedia": "Comedy",
    "Drama": "Drama", "Fantasía": "Fantasy", "Misterio": "Mystery",
    "Romance": "Romance", "Sci-Fi": "Sci-Fi", "Deportes": "Sports",
    "Terror": "Horror",
}


def get_anime_by_genre(genre: str, limit: int = 20) -> list[Movie]:
    anilist_genre = GENRES.get(genre, genre)
    data = cache.cached(
        "anilist_genre", anilist_genre,
        lambda: _post({"page": 1, "perPage": limit, "genre": anilist_genre,
                       "sort": ["POPULARITY_DESC"]}))
    results = _parse_page(data, limit)
    if not results:
        raise AniListError(f"No anime found for genre '{genre}'.")
    return results
