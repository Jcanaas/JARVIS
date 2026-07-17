"""Game metadata via the Steam Storefront API (unofficial, no key required).

Labeled "SteamDB" in the UI per product decision, but steamdb.info itself sits
behind Cloudflare anti-bot and has no public API — this hits Steam's own
storefront endpoints instead (store.steampowered.com/api/*), which are stable
and unauthenticated. See docs/plan-games.md §1.6 for the rationale.

This module does NOT handle downloading — only metadata discovery (matches the
role of actions/movie_search.py / actions/cinemeta.py for the Movies mode).
"""
from __future__ import annotations

import html as _html
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

import requests

STORE_API_BASE = "https://store.steampowered.com/api"
SEARCH_RESULTS_URL = "https://store.steampowered.com/search/results/"
REVIEWS_API_BASE = "https://store.steampowered.com/appreviews"
_LIBRARY_CAPSULE = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg"
# Steam's own wide (~1920px) hero/background art, used on the store's library
# pages — a fixed CDN path keyed only by appid, no API call needed. Far higher
# resolution than any capsule/thumbnail image, which is what a full-height
# hero banner needs to not look blurry when stretched across it.
_LIBRARY_HERO = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero.jpg"
# 460x215 landscape capsule — always present for every app, unlike the vertical
# library_600x900 poster which 404s for a chunk of appids. Used as the grid
# card's fallback image and the detail sidebar capsule.
_HEADER_CAPSULE = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
_TIMEOUT = 6
_CC, _LANG = "es", "spanish"
_CC_FALLBACK, _LANG_FALLBACK = "us", "english"  # some appids lack an ES ficha

# category1=998 restricts the storefront's own search-results endpoint to
# "Games" — it otherwise mixes in hardware (Steam Machine, Steam Deck), DLC,
# demos and soundtracks, none of which have a repack/download path. This is
# the same filter the Steam website's own category sidebar uses.
_GAMES_ONLY_CATEGORY = "998"
_ROW_RE = re.compile(
    r'data-ds-appid="(\d+)".*?<img src="([^"]+)".*?<span class="title">([^<]+)</span>',
    re.DOTALL,
)


class SteamCatalogError(RuntimeError):
    """Raised when a Steam Storefront lookup fails."""


@dataclass
class Screenshot:
    thumbnail_url: str
    full_url: str


@dataclass
class Trailer:
    name: str
    thumbnail_url: str
    video_url: str  # playable URL (HLS .m3u8, or mp4 when Steam still provides one)


@dataclass
class Game:
    """Game metadata from the Steam Storefront API."""

    appid: int
    title: str
    poster_url: str = ""      # vertical library capsule (600x900) — grid card
    header_url: str = ""      # horizontal capsule (460x215) — hero/detail
    backdrop_url: str = ""    # same landscape image, exposed under Movie's field
                               # name so the shared _HeroBanner picks it up instead
                               # of stretching the vertical poster_url
    thumb_url: str = ""       # small capsule from the search payload — served
                               # from a DIFFERENT CDN host (shared.*.steamstatic)
                               # than the fixed cdn.*/steam/apps paths, so it's
                               # the only image that resolves for some newer/
                               # regional appids. Last-ditch poster fallback.
    release_year: int = 0
    release_date_str: str = ""
    rating: float = 0.0       # metacritic score /100 scaled to /10 (matches Movie.rating)
    overview: str = ""
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)          # Steam "categories" (features)
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    screenshots: list[Screenshot] = field(default_factory=list)
    trailers: list[Trailer] = field(default_factory=list)
    review_summary: str = ""   # e.g. "Muy positivas"
    review_total: int = 0
    review_positive_pct: int = 0
    media_type: str = "game"  # lets shared panel code branch off movie/tv/game

    def to_dict(self) -> dict:
        return asdict(self)


# A real browser UA + a reused session: Steam's endpoints throttle the default
# python-requests UA more aggressively, and a fresh connection per call makes
# bursts (opening a mode, paging) look more like a scraper → 429.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"})
_RETRY_STATUSES = (429, 500, 502, 503)
_RETRY_BACKOFF = (0.6, 1.5, 3.0)  # seconds; also the retry count (len)


def _request(url: str, params: dict, retries: Optional[int] = None):
    """GET with retry+backoff on transient throttling/5xx. Returns the Response.

    retries: how many backoff retries to attempt (default: all of _RETRY_BACKOFF).
    Discovery rails that have a fallback endpoint pass a small number so they
    degrade fast instead of blocking on the full backoff ladder.

    Raises SteamCatalogError only after retries are exhausted (or on a hard
    network error), so callers can fall back to an alternative endpoint.
    """
    max_retries = len(_RETRY_BACKOFF) if retries is None else min(retries, len(_RETRY_BACKOFF))
    last_status = None
    for attempt in range(max_retries + 1):
        try:
            r = _SESSION.get(url, params=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            if attempt < max_retries:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            raise SteamCatalogError(
                f"Steam no responde ({exc.__class__.__name__}). Revisa tu conexión."
            ) from exc
        if r.status_code in _RETRY_STATUSES and attempt < max_retries:
            last_status = r.status_code
            time.sleep(_RETRY_BACKOFF[attempt])
            continue
        if not r.ok:
            raise SteamCatalogError(
                f"Steam devolvió HTTP {r.status_code}"
                + (" (límite de peticiones, prueba en un momento)"
                   if r.status_code == 429 else "")
            )
        return r
    raise SteamCatalogError(
        f"Steam sigue devolviendo HTTP {last_status} tras varios reintentos"
        + (" (límite de peticiones, prueba en un momento)" if last_status == 429 else "")
    )


def _http_get(url: str, params: dict) -> dict:
    try:
        return _request(url, params).json()
    except ValueError as exc:
        raise SteamCatalogError("Respuesta de Steam no válida.") from exc


def _parse_search_item(item: dict) -> Optional[Game]:
    appid = item.get("id", 0)
    title = item.get("name", "")
    if not appid or not title:
        return None
    return Game(
        appid=appid,
        title=title,
        poster_url=_LIBRARY_CAPSULE.format(appid=appid),
        header_url=_HEADER_CAPSULE.format(appid=appid),
        backdrop_url=_LIBRARY_HERO.format(appid=appid),
        thumb_url=item.get("tiny_image", ""),
        rating=float(item.get("metascore") or 0) / 10 if item.get("metascore") else 0.0,
    )


def search(query: str, limit: int = 20) -> list[Game]:
    """Search Steam's storefront for games by title.

    Args:
        query: Game title or text to search for
        limit: Max results

    Returns:
        List of Game objects.

    Raises:
        SteamCatalogError: If the request fails or no results are found.
    """
    query = query.strip()
    if not query:
        raise SteamCatalogError("Empty search query.")

    data = _http_get(f"{STORE_API_BASE}/storesearch/", {
        "term": query, "cc": _CC, "l": _LANG,
    })

    results: list[Game] = []
    for item in data.get("items", [])[:limit]:
        if item.get("type") != "app":
            continue
        game = _parse_search_item(item)
        if game:
            results.append(game)
    if not results:
        raise SteamCatalogError(f"No results found for '{query}'.")
    return results


def _fetch_review_summary(appid: int) -> tuple[str, int, int]:
    """('Muy positivas', total_reviews, positive_pct) — best-effort, never raises."""
    try:
        data = _http_get(f"{REVIEWS_API_BASE}/{appid}", {
            "json": 1, "language": "spanish", "purchase_type": "all", "num_per_page": 0,
        })
        q = data.get("query_summary") or {}
        total = int(q.get("total_reviews") or 0)
        positive = int(q.get("total_positive") or 0)
        pct = round(positive / total * 100) if total else 0
        desc = _REVIEW_DESC_ES.get(q.get("review_score_desc", ""), q.get("review_score_desc", ""))
        return desc, total, pct
    except Exception:
        return "", 0, 0


_REVIEW_DESC_ES = {
    "Overwhelmingly Positive": "Extremadamente positivas",
    "Very Positive": "Muy positivas",
    "Positive": "Positivas",
    "Mostly Positive": "Mayormente positivas",
    "Mixed": "Variadas",
    "Mostly Negative": "Mayormente negativas",
    "Negative": "Negativas",
    "Very Negative": "Muy negativas",
    "Overwhelmingly Negative": "Extremadamente negativas",
}


def _best_trailer_url(movie: dict) -> str:
    """Pick a directly playable URL from a Steam 'movies' entry.

    Older API responses included plain mp4 links; current ones are HLS/DASH
    manifests only. VLC (used for streaming elsewhere in the app) plays HLS
    (.m3u8) natively, so prefer that; fall back to mp4 if Steam ever includes it.
    """
    mp4 = movie.get("mp4") or {}
    if mp4.get("max"):
        return mp4["max"]
    if movie.get("hls_h264"):
        return movie["hls_h264"]
    webm = movie.get("webm") or {}
    return webm.get("max", "")


def _parse_details(appid: int, d: dict) -> Optional[Game]:
    if d.get("type") != "game":
        return None

    release_date = (d.get("release_date") or {}).get("date", "")
    release_year = 0
    for token in release_date.replace(",", " ").split():
        if token.isdigit() and len(token) == 4:
            release_year = int(token)
            break
    metacritic = (d.get("metacritic") or {}).get("score")

    screenshots = [
        Screenshot(thumbnail_url=s.get("path_thumbnail", ""), full_url=s.get("path_full", ""))
        for s in d.get("screenshots", []) if s.get("path_full")
    ]
    trailers = [
        Trailer(name=m.get("name", ""), thumbnail_url=m.get("thumbnail", ""),
                video_url=_best_trailer_url(m))
        for m in d.get("movies", [])
    ]
    trailers = [t for t in trailers if t.video_url]

    review_summary, review_total, review_pct = _fetch_review_summary(appid)

    return Game(
        appid=appid,
        title=d.get("name", ""),
        poster_url=_LIBRARY_CAPSULE.format(appid=appid),
        header_url=d.get("header_image", ""),
        backdrop_url=_LIBRARY_HERO.format(appid=appid),
        release_year=release_year,
        release_date_str=release_date,
        rating=float(metacritic) / 10 if metacritic else 0.0,
        overview=d.get("short_description", ""),
        genres=[g.get("description", "") for g in d.get("genres", []) if g.get("description")],
        tags=[c.get("description", "") for c in d.get("categories", []) if c.get("description")],
        developers=d.get("developers", []) or [],
        publishers=d.get("publishers", []) or [],
        screenshots=screenshots,
        trailers=trailers,
        review_summary=review_summary,
        review_total=review_total,
        review_positive_pct=review_pct,
    )


def get_details(appid: int) -> Optional[Game]:
    """Fetch full details (screenshots, trailers, reviews, tags…) for an appid.

    Returns None if the appid doesn't resolve to a real game (DLC, hardware,
    demo, soundtrack, etc. all report a different `type`).
    """
    data = _http_get(f"{STORE_API_BASE}/appdetails", {
        "appids": appid, "cc": _CC, "l": _LANG,
    })
    entry = data.get(str(appid)) or {}
    if not entry.get("success"):
        # Some appids have no ES ficha; retry with the US/English storefront.
        data = _http_get(f"{STORE_API_BASE}/appdetails", {
            "appids": appid, "cc": _CC_FALLBACK, "l": _LANG_FALLBACK,
        })
        entry = data.get(str(appid)) or {}
        if not entry.get("success"):
            return None

    return _parse_details(appid, entry.get("data") or {})


def _search_results(extra_params: dict, limit: int) -> list[Game]:
    """Fetch a page from the storefront's own search-results endpoint.

    This is the AJAX call the Steam store website itself makes for its
    "Top Sellers" / "New Releases" rails — unlike api/featuredcategories
    (only 10 top-seller items, no games-only filter) it paginates and
    supports category1=998 ("Games" only), so a single request yields a
    full, junk-free list without any per-item enrichment round-trip.
    """
    params = {
        "query": "", "start": 0, "count": limit, "dynamic_data": "",
        "supportedlang": _LANG, "infinite": 1, "cc": _CC, "l": _LANG,
        "category1": _GAMES_ONLY_CATEGORY,
        **extra_params,
    }
    # Fail after one quick retry: get_trending/get_new_releases fall back to
    # featuredcategories (a separate rate bucket) rather than block on the full
    # backoff ladder when search is throttling.
    data = _request(SEARCH_RESULTS_URL, params, retries=1).json()

    results: list[Game] = []
    seen: set[int] = set()
    for m in _ROW_RE.finditer(data.get("results_html") or ""):
        appid = int(m.group(1))
        if appid in seen:
            continue
        seen.add(appid)
        title = _html.unescape(m.group(3)).strip()
        if not title:
            continue
        results.append(_grid_game(appid, title, m.group(2)))
        if len(results) >= limit:
            break
    return results


def _grid_game(appid: int, title: str, thumb: str = "") -> Game:
    return Game(
        appid=appid, title=title,
        poster_url=_LIBRARY_CAPSULE.format(appid=appid),
        header_url=_HEADER_CAPSULE.format(appid=appid),
        backdrop_url=_LIBRARY_HERO.format(appid=appid),
        thumb_url=thumb,
    )


# Steam's /search/results endpoint is on a tight rate-limit bucket; the api/*
# endpoints are separate. Cache the discovery rails briefly so switching in and
# out of Games mode doesn't re-hit search, and fall back to featuredcategories
# (a different bucket) when search is throttling.
_CACHE_TTL = 300  # seconds
_cache: dict[str, tuple[float, list[Game]]] = {}


def _cached(key: str, fetch) -> list[Game]:
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    result = fetch()
    if result:
        _cache[key] = (now, result)
    return result


# Hardware / non-game items that featuredcategories mixes into top_sellers but
# category1=998 filters out on the search endpoint. Drop them by appid.
_HARDWARE_APPIDS = {353370, 353380, 596420, 1675200, 2519830, 4165910}


def _featured_fallback(key: str, limit: int) -> list[Game]:
    """Games-only best-effort list from api/featuredcategories (separate rate
    bucket). Used when the primary search endpoint is throttling."""
    data = _http_get(f"{STORE_API_BASE}/featuredcategories", {"cc": _CC, "l": _LANG})
    items = (data.get(key) or {}).get("items", [])
    out, seen = [], set()
    for it in items:
        appid = it.get("id", 0)
        title = it.get("name", "")
        if not appid or not title or appid in seen or appid in _HARDWARE_APPIDS:
            continue
        seen.add(appid)
        out.append(_grid_game(appid, title, it.get("header_image", "")))
        if len(out) >= limit:
            break
    return out


def get_trending(limit: int = 20) -> list[Game]:
    """Fetch currently top-selling games from the Steam storefront."""
    def fetch():
        try:
            return _search_results({"filter": "topsellers"}, limit)
        except SteamCatalogError:
            return _featured_fallback("top_sellers", limit)
    return _cached(f"trending:{limit}", fetch)


def get_new_releases(limit: int = 20) -> list[Game]:
    """Fetch recently released games from the Steam storefront."""
    def fetch():
        try:
            return _search_results({"sort_by": "Released_DESC"}, limit)
        except SteamCatalogError:
            return _featured_fallback("new_releases", limit)
    return _cached(f"new:{limit}", fetch)


def search_action(parameters: dict) -> str:
    """Voice/agent entry point for game search.

    parameters:
        query: game title to search
    """
    query = (parameters.get("query") or "").strip()
    if not query:
        return "¿Qué juego buscas?"

    try:
        results = search(query, limit=8)
        lines = [f"Encontré {len(results)} resultados:"]
        for i, g in enumerate(results, 1):
            rating = f" ★{g.rating:.1f}" if g.rating else ""
            lines.append(f"{i}. {g.title}{rating}")
        return "\n".join(lines)
    except SteamCatalogError as exc:
        return f"Error buscando juegos: {exc}"
