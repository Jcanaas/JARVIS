"""Torrent sources for anime, ranked so the unambiguous ones win.

Two sources, tried in order:

**AnimeTosho (primary).** Queried by *AniDB episode id* — the one identifier
that names a single episode of a single season with no ambiguity. We already
carry that id for every episode via api.ani.zip (actions.anime.mappings), it
was just unused until now. Crucially it reports `num_files`, so a release
containing exactly one file is *provably* the requested episode: there is no
second file it could have meant.

**Torrentio (fallback).** Keyed by `kitsu:<id>:<episode>`. Kept because it
indexes Spanish-language trackers AnimeTosho doesn't, and because a second
source is the whole point — during development the entire strem.fun domain
(Torrentio *and* the Kitsu catalog) went unreachable for a stretch, which is
exactly the failure this module exists to survive.

Why the ordering, measured rather than assumed (Attack on Titan S1 ep1,
2026-09-04):

    Torrentio   -> top hits are "Complete Collection S01-S04+OVA+Movies"
                   batches, and the SAME torrent came back with three
                   different fileIdx values tied on seeders. Each season
                   restarts its own "01, 02..." numbering inside the batch,
                   so "episode 1" genuinely is ambiguous there.
    AnimeTosho  -> 49 results for the episode's AniDB id, 46 of them
                   single-file. Top hit: 152 seeders, one clean .mkv.

That is the "me pone capítulos de otras temporadas" bug, fixed at the root
instead of by guessing from release titles.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import requests

from actions.doh import enable_for
from actions.trackers import TRACKERS

enable_for("animetosho.org")

_TOSHO_BASE = "https://feed.animetosho.org/json"
_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis)"}


def _fmt_size(num_bytes) -> str:
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


# A single ~24-minute episode above this is a Blu-ray remux-grade encode.
# Two problems at once, both measured on Attack on Titan S1 ep1: it is ~20
# Mbps so the browser has to buffer far more before it can start (the "le
# cuesta mucho cargar" complaint), and releases that big are overwhelmingly
# HEVC/10-bit even when the title never says so — the 3.67 GB "[MTBB] ... (BD
# 1080p)" that ffprobe showed as hevc/Main 10 passed the title check clean.
# Size is the signal the title does not give us.
_OVERSIZED_EPISODE_BYTES = 2_500_000_000


@dataclass
class TorrentResult:
    """One candidate release.

    Field names mirror actions.torrentio.Stream so the web UI and the Qt
    panel consume both without caring which source produced it.
    """

    title: str
    magnet: str
    seeders: int = 0
    size: str = ""
    provider: str = ""
    spanish: bool = False
    file_idx: int = -1
    # True when the release provably contains exactly one video file, so the
    # requested episode is the only thing it can be. Drives ranking.
    single_file: bool = False
    # Best-effort guess from the title; see is_browser_playable().
    browser_playable: bool = True
    # True for a single episode far larger than a streaming-friendly encode.
    oversized: bool = False
    # Hex infohash, used to scrape real swarm health.
    info_hash: str = ""
    # True once `seeders` has been replaced with a live scrape rather than
    # whatever the source claimed. Sources overstate badly — see
    # actions/anime/health.py.
    seeders_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "magnet": self.magnet,
            "seeders": self.seeders,
            "size": self.size,
            "provider": self.provider,
            "spanish": self.spanish,
            "file_idx": self.file_idx,
            "single_file": self.single_file,
            "browser_playable": self.browser_playable,
            "oversized": self.oversized,
            "seeders_verified": self.seeders_verified,
        }


def _augment_magnet(magnet: str) -> str:
    """Append our curated tracker list to a ready-made magnet.

    AnimeTosho's magnets already carry nyaa.tracker.wf — where anime swarms
    actually live, and which actions/trackers.py notably lacks — so we add to
    theirs rather than rebuilding from the infohash. bittorrent-tracker
    announces to every tracker in parallel, so a longer list costs no extra
    latency (see actions/trackers.py's docstring); duplicates are harmless.
    """
    from urllib.parse import quote
    if not magnet:
        return magnet
    existing = magnet.lower()
    extra = "".join(
        f"&tr={quote(t)}" for t in TRACKERS if quote(t).lower() not in existing
    )
    return magnet + extra


def _parse_tosho(item: dict) -> Optional[TorrentResult]:
    magnet = item.get("magnet_uri") or ""
    if not magnet:
        return None
    try:
        seeders = int(item.get("seeders") or 0)
    except (TypeError, ValueError):
        seeders = 0
    # AnimeTosho reports seeders as a huge sentinel when the tracker scrape
    # failed rather than when the swarm is genuinely enormous.
    if seeders > 100000:
        seeders = 0
    num_files = item.get("num_files")
    title = item.get("title") or item.get("torrent_name") or "Unknown"
    try:
        total_size = int(item.get("total_size") or 0)
    except (TypeError, ValueError):
        total_size = 0
    # Only meaningful per-episode: a legitimate season batch is big by design.
    oversized = bool(num_files == 1 and total_size > _OVERSIZED_EPISODE_BYTES)
    return TorrentResult(
        title=title,
        magnet=_augment_magnet(magnet),
        seeders=seeders,
        size=_fmt_size(item.get("total_size")),
        provider="AnimeTosho",
        spanish=_looks_spanish(title),
        # A single-file release needs no index: the daemon's "largest video
        # file" fallback lands on the only file there is.
        file_idx=-1,
        single_file=(num_files == 1),
        browser_playable=is_browser_playable(title),
        oversized=oversized,
        info_hash=str(item.get("info_hash") or "").lower(),
    )


# Release-title markers for things a browser <video> cannot decode.
#
# Inferred from the title rather than asked of the browser, because
# canPlayType() lies here: in the pywebview webview it answered "probably"
# for HEVC, and the release it green-lit
# ([MTBB] Shingeki no Kyojin - 01 (BD 1080p)) then sat at readyState 0
# forever. ffprobe on the actual stream showed hevc / Main 10 / yuv420p10le
# with Opus audio — and the bytes were arriving instantly (2 MB in 0.005s),
# so it was purely a decode failure, not a download one. Title inference is
# less precise but it is not actively wrong.
#
# Conservative by design: only *known-bad* markers demote a release. A title
# that says nothing about its codec is assumed playable, since most WEB
# releases are H.264/AAC.
_UNPLAYABLE_VIDEO = (
    "hevc", "x265", "h.265", "h265",
    "10-bit", "10bit", "hi10", "main10",
    "av1",
)
_UNPLAYABLE_AUDIO = ("ac3", "ac-3", "dts", "truehd", "flac")


def is_browser_playable(title: str) -> bool:
    """Best-effort guess at whether a browser <video> can decode this release.

    The browser is the only player, so a release failing this is effectively
    unplayable rather than merely inconvenient.
    """
    low = title.lower()
    if any(m in low for m in _UNPLAYABLE_VIDEO):
        return False
    return not any(m in low for m in _UNPLAYABLE_AUDIO)


_SPANISH_HINTS = ("castellano", "español", "espanol", "spanish", "latino", "[es]", "[spa]")


def _looks_spanish(title: str) -> bool:
    low = title.lower()
    return any(h in low for h in _SPANISH_HINTS)


def search_animetosho_by_eid(anidb_eid: int, limit: int = 25,
                             prefer_playable: bool = False) -> list[TorrentResult]:
    """Releases for one exact AniDB episode id."""
    if not anidb_eid:
        return []
    try:
        r = requests.get(_TOSHO_BASE, params={"eid": int(anidb_eid)},
                         timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = [t for item in data if (t := _parse_tosho(item))]
    return _rank(out, prefer_playable)[:limit]


def search_animetosho_by_aid(anidb_aid: int, limit: int = 25,
                             prefer_playable: bool = False) -> list[TorrentResult]:
    """Releases for a whole AniDB anime id — for films and for episodes whose
    per-episode id we could not resolve."""
    if not anidb_aid:
        return []
    try:
        r = requests.get(_TOSHO_BASE, params={"aid": int(anidb_aid)},
                         timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = [t for item in data if (t := _parse_tosho(item))]
    return _rank(out, prefer_playable)[:limit]


_BTIH_RE = re.compile(r"btih:([A-Za-z2-7]{32}|[A-Fa-f0-9]{40})")


def _info_hash_from_magnet(magnet: str) -> str:
    """Hex infohash out of a magnet, accepting both encodings.

    Torrentio builds hex magnets, AnimeTosho serves base32 ones; scraping
    needs the 20 raw bytes either way.
    """
    m = _BTIH_RE.search(magnet or "")
    if not m:
        return ""
    raw = m.group(1)
    if len(raw) == 40:
        return raw.lower()
    try:
        return base64.b32decode(raw.upper()).hex()
    except Exception:
        return ""


def verify_seeders(results: list[TorrentResult]) -> list[TorrentResult]:
    """Replace advertised seeder counts with a live BEP15 scrape.

    Sources overstate wildly — 541 advertised against 0 real was measured —
    so ranking on their numbers can put a completely dead torrent first.
    This is the difference between "no encuentra seeds" and working.

    Results the scrape says nothing about keep their advertised count and
    stay unverified: no answer means the trackers didn't know, not that the
    swarm is dead, and blanking them would be its own kind of lie.
    """
    hashes = [bytes.fromhex(r.info_hash) for r in results
              if r.info_hash and len(r.info_hash) == 40]
    if not hashes:
        return results
    try:
        from actions.anime import health
        live = health.scrape_seeders_cached(hashes)
    except Exception:
        return results
    for r in results:
        if not r.info_hash:
            continue
        try:
            key = bytes.fromhex(r.info_hash)
        except ValueError:
            continue
        if key in live:
            r.seeders = live[key]
            r.seeders_verified = True
    return results


def _rank(results: list[TorrentResult],
          prefer_playable: bool = False) -> list[TorrentResult]:
    """Rank candidates, most useful first.

    Tiers, in order: alive at all, then decodable, then unambiguously the
    right episode, then a sane size for streaming, then raw seeders.

    Being the right episode outranks being small on purpose: a fast-loading
    wrong episode is the exact bug this whole path exists to fix. Size only
    breaks ties among releases that are already correct — but it matters,
    because a 3.67 GB encode of a 24-minute episode makes the player look
    broken while it buffers. All of these were observed on Attack on Titan
    S1 ep1.
    """
    if prefer_playable:
        key = lambda t: (t.seeders > 0, t.browser_playable, t.single_file,
                         not t.oversized, t.seeders)
    else:
        key = lambda t: (t.seeders > 0, t.single_file, t.seeders)
    return sorted(results, key=key, reverse=True)


def _nyaa_results(kitsu_id: str, episode: int, limit: int) -> list[TorrentResult]:
    """Nyaa, adapted into TorrentResult.

    Carries the freshness AnimeTosho no longer has (it froze in May 2026),
    at the cost of matching by title instead of by episode id — hence the
    `single_file=False`: a Nyaa title tells us nothing reliable about how
    many files are inside.
    """
    titles = _titles_for(kitsu_id)
    if not titles:
        return []
    try:
        from actions.anime import nyaa
        raw = nyaa.search_episode(titles, episode=episode, limit=limit)
    except Exception:
        return []

    out = []
    for item in raw:
        info_hash = item["info_hash"]
        title = item["title"]
        magnet = _augment_magnet(
            f"magnet:?xt=urn:btih:{info_hash}"
            f"&dn={urllib.parse.quote(title)}")
        out.append(TorrentResult(
            title=title,
            magnet=magnet,
            seeders=item["seeders"],
            size=_fmt_size(item["size_bytes"]),
            provider="Nyaa",
            spanish=_looks_spanish(title),
            file_idx=-1,
            single_file=False,
            browser_playable=is_browser_playable(title),
            oversized=False,
            info_hash=info_hash,
        ))
    return out


def _titles_for(kitsu_id: str) -> list[str]:
    """Romaji first, then English — Nyaa release names use romaji far more."""
    try:
        from actions.anime import mappings
        t = mappings.mappings(kitsu_id=kitsu_id).titles
    except Exception:
        return []
    ordered = [t.get("x-jat"), t.get("en"), t.get("ja")]
    return [x for x in ordered if x]


def _torrentio_fallback(kitsu_id: str, episode: int, total_episodes: int,
                        limit: int) -> list[TorrentResult]:
    """Torrentio, adapted into TorrentResult."""
    from actions import torrentio
    if episode:
        video_id, stream_type = f"kitsu:{kitsu_id}:{episode}", "series"
    elif total_episodes > 1:
        video_id, stream_type = f"kitsu:{kitsu_id}:1", "series"
    else:
        video_id, stream_type = f"kitsu:{kitsu_id}", "movie"
    try:
        streams = torrentio.search_by_id(video_id, stream_type=stream_type,
                                         limit=limit)
    except Exception:
        return []
    return [
        TorrentResult(
            title=s.title, magnet=s.magnet, seeders=s.seeders, size=s.size,
            provider=s.provider or "Torrentio", spanish=s.spanish,
            file_idx=s.file_idx,
            browser_playable=is_browser_playable(s.title),
            info_hash=_info_hash_from_magnet(s.magnet),
            # Torrentio never tells us the file count, so we cannot claim a
            # release is unambiguous — only AnimeTosho's num_files can.
            single_file=False,
        )
        for s in streams
    ]


def find_episode(kitsu_id: str, episode: int = 0, total_episodes: int = 0,
                 limit: int = 25, include_fallback: bool = True,
                 prefer_playable: bool = False,
                 verify: bool = True) -> list[TorrentResult]:
    """Best available releases for one episode, AnimeTosho first.

    Sequential on purpose. An earlier version fired both sources at once to
    hide the fallback's latency, but a ThreadPoolExecutor waits for every
    submitted task on exit — so whenever Torrentio was slow or down (a 12s
    connect timeout, measured) the *successful* AnimeTosho path paid for it
    too. Querying Torrentio only when AnimeTosho comes back empty makes the
    common path one request, and the fallback's cost lands only on the calls
    that actually need it.

    AnimeTosho's results are returned alone rather than merged with
    Torrentio's: mixing provably-single-file releases with unlabelled batches
    would put the ambiguity straight back into the list the auto-picker reads
    from.

    `verify` scrapes real seeder counts before ranking (see verify_seeders);
    turn it off only where the extra few seconds matter more than the numbers
    being true.
    """
    kitsu_id = str(kitsu_id).replace("kitsu:", "")
    if not kitsu_id:
        return []

    anidb_eid, anidb_aid = _resolve_anidb_ids(kitsu_id, episode)

    # Nyaa and AnimeTosho are queried together and merged rather than used as
    # first-choice/fallback, because neither covers the whole catalogue:
    # AnimeTosho is precise (AniDB episode ids) but frozen since May 2026, and
    # Nyaa is current but matches by title. Ranking on verified swarm health
    # sorts out which of the two actually produced something usable.
    with ThreadPoolExecutor(max_workers=2) as pool:
        tosho_f = pool.submit(_animetosho_for, anidb_eid, anidb_aid, limit,
                              prefer_playable)
        nyaa_f = pool.submit(_nyaa_results, kitsu_id, episode, limit)
        candidates = tosho_f.result() + nyaa_f.result()

    if not candidates and include_fallback:
        candidates = _torrentio_fallback(kitsu_id, episode, total_episodes, limit)

    candidates = _dedupe(candidates)

    # Rank *after* verifying, never before: the whole point is that the
    # advertised numbers are wrong, so sorting on them first would just pick
    # a different wrong winner.
    if verify:
        candidates = verify_seeders(candidates)
    return _rank(candidates, prefer_playable)[:limit]


def _dedupe(results: list[TorrentResult]) -> list[TorrentResult]:
    """One entry per infohash — the same release is listed by both sources.

    Keeps whichever copy carries more information: a result that knows it is
    a single file (AnimeTosho) beats the same torrent seen through Nyaa,
    which cannot tell.
    """
    best: dict[str, TorrentResult] = {}
    loose: list[TorrentResult] = []
    for r in results:
        if not r.info_hash:
            loose.append(r)
            continue
        prev = best.get(r.info_hash)
        if prev is None or (r.single_file and not prev.single_file):
            best[r.info_hash] = r
    return list(best.values()) + loose


def _animetosho_for(anidb_eid: int, anidb_aid: int, limit: int,
                    prefer_playable: bool = False) -> list[TorrentResult]:
    if anidb_eid:
        hits = search_animetosho_by_eid(anidb_eid, limit=limit,
                                        prefer_playable=prefer_playable)
        if hits:
            return hits
    # No per-episode id (films, specials, gaps in ani.zip) — fall back to the
    # whole series, where a single-file release is still identifiable.
    if not anidb_aid:
        return []
    return search_animetosho_by_aid(anidb_aid, limit=limit,
                                    prefer_playable=prefer_playable)


def _resolve_anidb_ids(kitsu_id: str, episode: int) -> tuple[int, int]:
    """(anidb_eid, anidb_aid) for one episode, or (0, 0) when unknown."""
    from actions.anime import mappings
    anidb_aid = 0
    try:
        anidb_aid = mappings.mappings(kitsu_id=kitsu_id).anidb_id
    except Exception:
        pass
    if not episode:
        return 0, anidb_aid
    try:
        for ep in mappings.episodes(kitsu_id=kitsu_id, include_specials=False):
            if ep.number == episode:
                return ep.anidb_eid, anidb_aid
    except Exception:
        pass
    return 0, anidb_aid
