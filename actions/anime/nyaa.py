"""Anime releases from Nyaa's public RSS.

Added because AnimeTosho — until now our primary source — **stopped indexing
new releases in May 2026 and shuts down entirely in October 2026** (its own
notice, and confirmed live on 2026-09-05: the newest item it returns is dated
2026-05-08). It is now an archive. Excellent for older series, useless for
anything currently airing, and gone within weeks.

Nyaa covers exactly that gap: releases from the same day, with real seeder
counts in the feed itself (`nyaa:seeders`).

The trade-off is precision. Nyaa has no episode-level API — only full-text
search — so a query can return the wrong season of a franchise ("Sousou no
Frieren 01" returns both the S1 and S2 batches). That is why this is a
*complement* to AnimeTosho rather than a replacement: Tosho matches by AniDB
episode id and cannot be wrong about which episode a release is; Nyaa is
fresh. Merging both and ranking on verified swarm health (actions/anime/
health.py) gets the useful half of each.

Category 1_2 is "Anime — English-translated"; 1_0 is all anime.
"""
from __future__ import annotations

import re
import urllib.parse

import requests
# defusedxml, not the stdlib parser: this is XML fetched from a public site,
# so it is untrusted input and the stdlib parser is vulnerable to entity
# expansion attacks.
from defusedxml import ElementTree as ET

from actions.doh import enable_for

enable_for("nyaa.si")

_BASE = "https://nyaa.si/"
_TIMEOUT = 15
_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis)"}
_NS = {"nyaa": "https://nyaa.si/xmlns/nyaa"}

_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT])i?B", re.IGNORECASE)
_UNIT_BYTES = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


class NyaaError(RuntimeError):
    """Raised when the Nyaa lookup fails."""


def _size_to_bytes(text: str) -> int:
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _UNIT_BYTES[m.group(2).upper()])


def _int(node, default: int = 0) -> int:
    try:
        return int((node.text or "").strip())
    except (AttributeError, TypeError, ValueError):
        return default


def search(query: str, category: str = "1_2", limit: int = 50) -> list[dict]:
    """Raw Nyaa results for a full-text query, newest first.

    Returns plain dicts; converting to TorrentResult is the caller's job so
    this module stays free of the ranking model.
    """
    query = (query or "").strip()
    if not query:
        return []
    url = (f"{_BASE}?page=rss&q={urllib.parse.quote(query)}"
           f"&c={category}&f=0")
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError, ValueError) as exc:
        raise NyaaError(f"Nyaa request failed: {exc}") from exc

    out = []
    for item in root.findall(".//item")[:limit]:
        info_hash = item.findtext("nyaa:infoHash", default="", namespaces=_NS)
        title = (item.findtext("title") or "").strip()
        if not info_hash or not title:
            continue
        out.append({
            "title": title,
            "info_hash": info_hash.strip().lower(),
            "seeders": _int(item.find("nyaa:seeders", _NS)),
            "leechers": _int(item.find("nyaa:leechers", _NS)),
            "size_bytes": _size_to_bytes(
                item.findtext("nyaa:size", default="", namespaces=_NS)),
            "trusted": (item.findtext("nyaa:trusted", default="No",
                                      namespaces=_NS) or "").strip() == "Yes",
        })
    return out


# "- 05", "- 05v2", "[05]", "S02E05", "E05"
def _episode_patterns(episode: int) -> list[re.Pattern]:
    forms = {str(episode), f"{episode:02d}", f"{episode:03d}"}
    pats = []
    for e in forms:
        pats.append(re.compile(rf"(?:^|[^0-9a-z])s\d{{1,2}}\s*e{e}(?![0-9])", re.I))
        pats.append(re.compile(rf"(?:^|[^0-9a-z])(?:e|ep|episode)?[ _.\-]{e}(?:v\d)?(?![0-9])", re.I))
        pats.append(re.compile(rf"\[{e}\]", re.I))
    return pats


# The leading (?<![A-Za-z]) matters: without it, "S2 - 01~12" matches as
# "2 - 01" (the season digit against the range start), which is a backwards
# range AND consumes the real "01~12" so it is never seen.
_RANGE_RE = re.compile(r"(?<![A-Za-z])(\d{1,4})\s*[-~]\s*(\d{1,4})(?![0-9])")


def _covers_episode(title: str, episode: int) -> bool:
    """True if a title plausibly contains this episode.

    Either it names the episode directly, or it is a range/batch whose
    bounds span it — "(01-28)", "01~12". Titles that say neither are
    rejected, which is what keeps a full-text search from returning some
    other episode of the same show.
    """
    if not episode:
        return True
    stem = title
    for m in _RANGE_RE.finditer(stem):
        lo, hi = int(m.group(1)), int(m.group(2))
        # Guard against matching a resolution ("1080") or a year range.
        if lo < hi <= 9999 and lo <= episode <= hi and hi - lo >= 1:
            return True
    return any(p.search(stem) for p in _episode_patterns(episode))


def search_episode(titles: list[str], episode: int = 0,
                   limit: int = 25) -> list[dict]:
    """Results for one episode, trying each candidate title until one hits.

    `titles` should be ordered most-canonical-first (romaji, then English):
    Nyaa's index is built from release names, which use romaji far more often
    than the English title.
    """
    for title in titles:
        if not title:
            continue
        query = f"{title} {episode:02d}" if episode else title
        try:
            raw = search(query, limit=limit * 2)
        except NyaaError:
            continue
        hits = [r for r in raw if _covers_episode(r["title"], episode)]
        if hits:
            return hits[:limit]
    return []
