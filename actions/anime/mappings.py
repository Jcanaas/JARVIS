"""Cross-site ids and episode data via api.ani.zip.

One request answers three questions we previously had no single source for:

  * **Cross-site ids.** Kitsu gives us a kitsu_id and nothing else, but AniSkip
    is keyed by MAL id and the non-Torrentio torrent sources are keyed by AniDB
    id. ani.zip returns all of them together, and accepts any one of them as
    the lookup key (verified live against kitsu_id, mal_id and anilist_id).
  * **Episode metadata.** Richer than the Kitsu addon's: TheTVDB stills,
    per-episode runtime, synopsis, and titles in ~7 languages.
  * **Absolute episode numbering.** The episode keys are absolute — the same
    numbering Kitsu and Torrentio use, so `episodes()[n].number` is exactly what
    goes into a `kitsu:<id>:<ep>` stream id.

A caveat measured against real data, not assumed: the `seasonNumber` /
`episodeNumber` fields come from TheTVDB and are **unreliable for long-running
shows**. One Piece returns 1166 of its 1180 episodes as "season 1" (keys
1..1180) and a "season 2" holding 14 episodes with keys 9..22 — overlapping and
useless. So absolute numbering is the primary key here and season/episode is
advisory only; anything that must be correct uses `number`.

Response shape (the parts we use):

    {"mappings": {"kitsu_id": 46474, "mal_id": 52991, "anilist_id": 154587,
                  "anidb_id": 17617, "thetvdb_id": 424536, "imdb_id": "tt…"},
     "titles":   {"es": "…", "en": "…", "ja": "…", "x-jat": "…"},
     "episodes": {"1": {…}, "2": {…}, "S1": {…}}}

Episode keys are the episode number for real episodes and "S<n>" for specials.
The two shapes differ in more than the key — specials carry `airdate`/`length`
where episodes carry `airDate`/`runtime`, and have no season/absolute numbers —
so both spellings are accepted throughout.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

from actions.anime import cache
from actions.doh import enable_for

enable_for("ani.zip")

_BASE = "https://api.ani.zip"
_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis)"}

# Preference order for a display title: Spanish first (the app is Spanish),
# then English, then the romanised Japanese ("x-jat"), then Japanese.
_TITLE_LANGS = ("es", "en", "x-jat", "ja")


class MappingsError(RuntimeError):
    """Raised when the ani.zip lookup fails."""


@dataclass
class Mapping:
    """Cross-site ids for one anime. Missing ids are 0 / ""."""

    kitsu_id: str = ""
    mal_id: int = 0
    anilist_id: int = 0
    anidb_id: int = 0
    tvdb_id: int = 0
    imdb_id: str = ""
    tmdb_id: str = ""
    titles: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        for lang in _TITLE_LANGS:
            if self.titles.get(lang):
                return self.titles[lang]
        return next((v for v in self.titles.values() if v), "")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["title"] = self.title
        return d


@dataclass
class Episode:
    """One episode.

    `number` is the absolute episode number (ani.zip's own key) and is the
    field to trust — it is what `kitsu:<id>:<ep>` expects. `season` and
    `season_episode` mirror TheTVDB and are advisory; see the module docstring.
    Specials have `number == 0` and sort last.
    """

    number: int = 0
    absolute: int = 0
    season: int = 0
    season_episode: int = 0
    title: str = ""
    aired: str = ""          # YYYY-MM-DD
    airs_at_utc: str = ""    # RFC3339, when known
    runtime: int = 0         # minutes
    overview: str = ""
    image: str = ""
    anidb_eid: int = 0
    special: bool = False
    raw_key: str = ""        # "12" or "S3" — the id ani.zip filed it under

    def to_dict(self) -> dict:
        return asdict(self)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _http_get(params: dict) -> dict:
    try:
        r = requests.get(f"{_BASE}/mappings", params=params,
                         timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise MappingsError(f"ani.zip request failed: {exc}") from exc
    except ValueError as exc:
        raise MappingsError(f"Invalid ani.zip response: {exc}") from exc


def _query_params(kitsu_id=None, mal_id=None, anilist_id=None,
                  anidb_id=None) -> tuple[dict, str]:
    """Build the query and the cache key for whichever id was supplied."""
    for name, value in (("kitsu_id", kitsu_id), ("mal_id", mal_id),
                        ("anilist_id", anilist_id), ("anidb_id", anidb_id)):
        if value:
            clean = str(value).replace("kitsu:", "").strip()
            if clean:
                return {name: clean}, f"{name}-{clean}"
    raise MappingsError("No id provided (need one of kitsu/mal/anilist/anidb).")


def _fetch(kitsu_id=None, mal_id=None, anilist_id=None, anidb_id=None) -> dict:
    """Full ani.zip payload, cached for 30 days (these ids never change)."""
    params, key = _query_params(kitsu_id, mal_id, anilist_id, anidb_id)
    return cache.cached("mappings", key, lambda: _http_get(params))


def _parse_mapping(data: dict) -> Mapping:
    m = data.get("mappings") or {}
    return Mapping(
        kitsu_id=str(m.get("kitsu_id") or ""),
        mal_id=_int(m.get("mal_id")),
        anilist_id=_int(m.get("anilist_id")),
        anidb_id=_int(m.get("anidb_id")),
        tvdb_id=_int(m.get("thetvdb_id")),
        imdb_id=str(m.get("imdb_id") or ""),
        tmdb_id=str(m.get("themoviedb_id") or ""),
        titles={k: v for k, v in (data.get("titles") or {}).items() if v},
    )


def _episode_title(raw: dict, key: str) -> str:
    titles = raw.get("title")
    if isinstance(titles, dict):
        for lang in _TITLE_LANGS:
            if titles.get(lang):
                return titles[lang]
        first = next((v for v in titles.values() if v), "")
        if first:
            return first
    elif isinstance(titles, str) and titles:
        return titles
    return f"Episodio {key}"


def _parse_episode(key: str, raw: dict) -> Episode:
    special = not key.isdigit()
    # Specials use `airdate`/`length`; regular episodes use `airDate`/`runtime`.
    aired = str(raw.get("airDate") or raw.get("airdate") or "")[:10]
    return Episode(
        number=0 if special else _int(key),
        absolute=_int(raw.get("absoluteEpisodeNumber"), _int(key) if not special else 0),
        season=0 if special else _int(raw.get("seasonNumber"), 1),
        season_episode=0 if special else _int(raw.get("episodeNumber")),
        title=_episode_title(raw, key),
        aired=aired,
        airs_at_utc=str(raw.get("airDateUtc") or ""),
        runtime=_int(raw.get("runtime")) or _int(raw.get("length")),
        overview=str(raw.get("overview") or ""),
        image=str(raw.get("image") or ""),
        anidb_eid=_int(raw.get("anidbEid")),
        special=special,
        raw_key=key,
    )


_SPECIAL_KEY_RE = re.compile(r"^S(\d+)$", re.IGNORECASE)


def _sort_key(ep: Episode) -> tuple:
    """Real episodes in order, then specials in their own numeric order."""
    if not ep.special:
        return (0, ep.number)
    m = _SPECIAL_KEY_RE.match(ep.raw_key)
    return (1, _int(m.group(1)) if m else 0)


def mappings(*, kitsu_id=None, mal_id=None, anilist_id=None,
             anidb_id=None) -> Mapping:
    """Cross-site ids for one anime, looked up by whichever id you have."""
    return _parse_mapping(_fetch(kitsu_id, mal_id, anilist_id, anidb_id))


def episodes(*, kitsu_id=None, mal_id=None, anilist_id=None, anidb_id=None,
             include_specials: bool = True) -> list[Episode]:
    """Episode list for one anime, ordered, specials last."""
    data = _fetch(kitsu_id, mal_id, anilist_id, anidb_id)
    out = [_parse_episode(k, v) for k, v in (data.get("episodes") or {}).items()
           if isinstance(v, dict)]
    if not include_specials:
        out = [e for e in out if not e.special]
    return sorted(out, key=_sort_key)


def resolve_mal_id(kitsu_id: str) -> int:
    """MAL id for a Kitsu id — what AniSkip needs and Kitsu doesn't provide.

    Returns 0 rather than raising: every caller so far treats a missing MAL id
    as "skip that feature", not as an error worth surfacing.
    """
    try:
        return mappings(kitsu_id=kitsu_id).mal_id
    except MappingsError:
        return 0


def absolute_episode(kitsu_id: str, season: int, episode: int) -> Optional[int]:
    """Best-effort absolute number for a season/episode pair, else None.

    Only for reconciling sources that index by season (a TVDB-shaped release
    name, say) against our absolute ids. Unreliable by construction — see the
    season-numbering caveat in the module docstring — so callers must handle
    None and should prefer an absolute number whenever they already have one.
    """
    try:
        eps = episodes(kitsu_id=kitsu_id, include_specials=False)
    except MappingsError:
        return None
    for ep in eps:
        if ep.season == season and ep.season_episode == episode:
            return ep.number or ep.absolute or None
    # Single-season shows: season/episode and absolute are the same thing.
    if season <= 1:
        for ep in eps:
            if ep.number == episode:
                return ep.number
    return None
