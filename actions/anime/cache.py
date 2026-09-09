"""TTL disk cache for anime lookups.

Every anime screen used to hit the network on open: the trending rail, the
genre pills and the episode list all re-fetched on every visit, so switching
tabs cost a round trip each time. This caches those responses on disk under
memory/anime_cache/<namespace>/<key>.json with a per-namespace TTL.

Cache entries are plain JSON, so a corrupt or half-written file is treated as a
miss rather than an error — nothing here is authoritative, it's all
re-fetchable.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from actions.paths import memory_path

_ROOT = memory_path("anime_cache")

# TTLs in seconds, by namespace. Airing shows move; ids never do.
TTL = {
    "catalog_trending": 6 * 3600,
    "catalog_airing": 3600,
    "catalog_genre": 12 * 3600,
    "catalog_search": 3600,
    "meta": 24 * 3600,
    "episodes": 6 * 3600,
    "episodes_airing": 3600,   # same data, shorter TTL while a show is running
    "mappings": 30 * 86400,    # cross-site ids don't change
    "aniskip": 7 * 86400,      # community-submitted, essentially static once aired
    "swarm": 600,              # live seeder counts; drift slowly, re-checked often
    "anilist_trending": 6 * 3600,
    "anilist_airing": 3600,
    "anilist_genre": 12 * 3600,
    "anilist_search": 3600,
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _key_to_filename(key: str) -> str:
    """Readable filename for simple keys, hashed for anything else.

    Keeping "trending" or "12345" legible makes the cache directory
    inspectable by hand; a search query like "Fullmetal: Brotherhood" would be
    an invalid or ambiguous filename, so those get hashed instead.
    """
    safe = _SAFE.sub("_", key)
    if safe == key and 0 < len(key) <= 64:
        return f"{key}.json"
    return f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"


def _path(namespace: str, key: str) -> Path:
    return _ROOT / _SAFE.sub("_", namespace) / _key_to_filename(key)


def get(namespace: str, key: str, ttl: Optional[int] = None) -> Any:
    """Return the cached value, or None when missing, stale or unreadable."""
    if ttl is None:
        ttl = TTL.get(namespace, 3600)
    p = _path(namespace, key)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(raw["stored_at"]) > ttl:
            return None
        return raw["value"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set(namespace: str, key: str, value: Any) -> None:
    """Store a value. Failures are silent — a cache miss is never fatal."""
    p = _path(namespace, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"stored_at": time.time(), "value": value})
        # Write to a sibling then rename: a crash mid-write would otherwise
        # leave a truncated file that every later read has to reject.
        tmp = p.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        pass


def get_stale(namespace: str, key: str) -> Any:
    """Return a cached value ignoring its TTL, or None if there is none.

    Only for the serve-stale-on-error path in `cached()` — normal reads must
    go through `get()` so expiry still means something.
    """
    return get(namespace, key, ttl=float("inf"))


def cached(namespace: str, key: str, producer: Callable[[], Any],
           ttl: Optional[int] = None) -> Any:
    """Return the cached value, else call `producer` and cache what it returns.

    **Serve-stale-on-error:** if the producer raises and we still hold an
    expired entry, that expired entry is returned instead of propagating the
    error. A 22-hour-old trending list beats a dead panel — and this is not
    hypothetical: during development the entire strem.fun domain (which serves
    both the Kitsu catalog *and* Torrentio) went unreachable for a stretch,
    and every catalog call raised even though perfectly usable data was
    sitting on disk, expired by a few hours. The error only propagates when
    there is genuinely nothing cached to fall back on.

    Empty results are not cached, so a transient upstream hiccup returning []
    doesn't get pinned for the whole TTL.
    """
    hit = get(namespace, key, ttl)
    if hit is not None:
        return hit
    try:
        value = producer()
    except Exception:
        stale = get_stale(namespace, key)
        if stale is not None:
            return stale
        raise
    if value:
        set(namespace, key, value)
    return value


def invalidate(namespace: str, key: Optional[str] = None) -> None:
    """Drop one entry, or a whole namespace when `key` is None."""
    try:
        if key is not None:
            _path(namespace, key).unlink(missing_ok=True)
            return
        for f in (_ROOT / _SAFE.sub("_", namespace)).glob("*.json"):
            f.unlink(missing_ok=True)
    except OSError:
        pass
