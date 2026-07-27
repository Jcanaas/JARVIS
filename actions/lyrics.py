"""Time-synced lyrics for the karaoke overlay in the music player.

Fetches LRC (line-synced) lyrics via `syncedlyrics` (aggregates Musixmatch/
NetEase/lrclib/etc.) and exposes a simple "what line is playing right now"
lookup so the UI just needs the current playback position.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
import time

_LRC_LINE_RE = re.compile(r"\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\](.*)")

# Noise YT Music / YouTube titles carry that hurts lyric-provider matching.
_TITLE_NOISE_RE = re.compile(
    r"\s*[\(\[][^)\]]*(official|oficial|video|audio|lyric|letra|hd|4k|remaster"
    r"|visualizer|explicit)[^)\]]*[\)\]]",
    re.IGNORECASE,
)

# Successful lookups only — a transient network failure must not be cached
# as "this song has no lyrics" for the rest of the session (lru_cache would).
_cache: dict[str, tuple[tuple[float, str], ...]] = {}
_cache_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[Lyrics] {msg}", file=sys.stderr)


def _clean_title(title: str) -> str:
    title = _TITLE_NOISE_RE.sub("", str(title or ""))
    return " ".join(title.split()).strip()


def _first_artist(artists: str) -> str:
    text = str(artists or "").replace(" - Topic", "")
    for sep in (",", " feat.", " ft.", " x ", " & "):
        if sep in text:
            text = text.split(sep)[0]
    return text.strip()


def _parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    lines: list[tuple[float, str]] = []
    for raw in (lrc_text or "").splitlines():
        match = _LRC_LINE_RE.match(raw.strip())
        if not match:
            continue
        minutes, seconds, text = match.groups()
        text = text.strip()
        if not text:
            continue
        timestamp = int(minutes) * 60 + float(seconds)
        lines.append((timestamp, text))
    lines.sort(key=lambda item: item[0])
    return lines


# Provider threads race each other; a query gives up after this long instead
# of waiting out a slow/rate-limited one (Lrclib alone has been observed
# taking 10+ seconds while Musixmatch/NetEase answer the same query in ~1s).
_PROVIDERS = ("Musixmatch", "NetEase", "Lrclib", "Megalobiz")
_QUERY_TIMEOUT_S = 4.0


def _search_provider(provider: str, query: str, result_q: "queue.Queue") -> None:
    try:
        import syncedlyrics
        lrc = syncedlyrics.search(query, synced_only=True, providers=[provider])
    except Exception as exc:
        _log(f"error {provider} con {query!r}: {type(exc).__name__}: {exc}")
        return
    if not lrc:
        return
    lines = tuple(_parse_lrc(lrc))
    if lines:
        result_q.put((provider, lines))


def get_synced_lyrics(title: str, artists: str) -> tuple[tuple[float, str], ...]:
    """Return ((timestamp_seconds, line), ...) for a track, or () if unavailable.

    Fires every (provider, query-variant) combination at once and returns the
    first synced hit — a single hard timeout caps the whole lookup regardless
    of how many providers/variants are in flight, so a slow or rate-limited
    provider (Lrclib alone has been observed taking 10+ seconds) never holds
    up a query another provider already answered in ~1s. Best-effort: karaoke
    is a nice-to-have overlay, never worth surfacing an error over — but
    failures are logged to stderr so they're diagnosable.
    """
    clean_title = _clean_title(title)
    artist = _first_artist(artists)
    if not clean_title:
        return ()

    cache_key = f"{clean_title}|{artist}".lower()
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    queries = [f"{clean_title} {artist}".strip()]
    if artist:
        queries.append(clean_title)

    _log(f"buscando: {queries!r} × {len(_PROVIDERS)} providers en paralelo")
    result_q: "queue.Queue" = queue.Queue()
    for query in queries:
        for provider in _PROVIDERS:
            threading.Thread(
                target=_search_provider, args=(provider, query, result_q), daemon=True
            ).start()

    deadline = time.monotonic() + _QUERY_TIMEOUT_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            provider, lines = result_q.get(timeout=remaining)
        except queue.Empty:
            break
        if not lines:
            continue
        _log(f"encontrada ({len(lines)} líneas) en {provider}")
        with _cache_lock:
            if len(_cache) > 64:
                _cache.clear()
            _cache[cache_key] = lines
        return lines

    _log(f"sin resultados (timeout {_QUERY_TIMEOUT_S}s): {queries!r}")
    return ()


def current_line(lines: tuple[tuple[float, str], ...], position: float) -> str:
    """Return the lyric line active at `position` seconds, or "" if none yet."""
    active = ""
    for timestamp, text in lines:
        if timestamp > position:
            break
        active = text
    return active
