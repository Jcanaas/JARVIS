"""Opening/ending/recap skip windows via AniSkip (api.aniskip.com).

AniSkip is keyed by MAL id, which the Kitsu-based catalog never carries on its
own — actions.anime.mappings.resolve_mal_id() (api.ani.zip) is what unblocks
this, same as it unblocks everything else that needs a MAL id.

Verified live (2026-09-03):
  * A known episode (Frieren ep 1, mal_id 52991) returns "op" 3.2s-93.2s and
    "ed" ~1460s-1560s — matched against whichever stored episodeLength is
    closest to the one passed in, so passing the real episode duration gives
    a tighter match; omitting it (or passing 0) still returns *a* result, just
    possibly off by a few seconds if multiple release lengths were submitted.
  * An anime AniSkip has never seen returns HTTP 404 with a JSON body
    ({"found": false, "results": []}) — not a network error, a legitimate
    "nothing here" that must not raise.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import requests

from actions.anime import cache
from actions.doh import enable_for

enable_for("aniskip.com")

_BASE = "https://api.aniskip.com/v2/skip-times"
_TIMEOUT = 8
_TYPES = ("op", "ed", "recap")


@dataclass
class SkipWindow:
    """One skippable stretch of an episode."""

    kind: str       # "op" | "ed" | "recap"
    start_s: float
    end_s: float

    def to_dict(self) -> dict:
        return asdict(self)


def _fetch(mal_id: int, episode: int, episode_length_s: float) -> list[dict]:
    params = [("types", t) for t in _TYPES]
    if episode_length_s > 0:
        params.append(("episodeLength", episode_length_s))
    url = f"{_BASE}/{mal_id}/{episode}/"
    try:
        r = requests.get(url, params=params, timeout=_TIMEOUT)
    except requests.RequestException:
        return []
    if r.status_code == 404:
        return []  # "no skip data for this episode" — not an error
    try:
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    if not data.get("found"):
        return []

    out = []
    for item in data.get("results", []):
        interval = item.get("interval") or {}
        kind = item.get("skipType", "")
        if kind not in _TYPES:
            continue
        try:
            start = float(interval.get("startTime") or 0)
            end = float(interval.get("endTime") or 0)
        except (TypeError, ValueError):
            continue
        if end > start:
            out.append({"kind": kind, "start_s": start, "end_s": end})
    return out


def get_skip_windows(mal_id: int, episode: int,
                     episode_length_s: float = 0) -> list[SkipWindow]:
    """Skip windows for one episode, or [] if unknown/unavailable.

    Caches per (mal_id, episode, rounded episode length) — skip times are
    community-submitted and essentially static once an episode has aired, so
    a long TTL is safe. A genuine "no data" response isn't cached (see
    actions.anime.cache.cached), so a not-yet-submitted episode gets retried
    on the next view rather than pinned empty for a week.
    """
    if not mal_id or not episode:
        return []
    key = f"{mal_id}-{episode}-{int(episode_length_s)}"
    raw = cache.cached("aniskip", key,
                       lambda: _fetch(mal_id, episode, episode_length_s))
    return [SkipWindow(**w) for w in (raw or [])]
