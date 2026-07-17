"""Time-synced lyrics for the karaoke overlay in the music player.

Fetches LRC (line-synced) lyrics via `syncedlyrics` (aggregates Musixmatch/
NetEase/lrclib/etc.) and exposes a simple "what line is playing right now"
lookup so the UI just needs the current playback position.
"""
from __future__ import annotations

import re
from functools import lru_cache

_LRC_LINE_RE = re.compile(r"\[(\d{1,2}):(\d{2}(?:\.\d{1,2})?)\](.*)")


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


@lru_cache(maxsize=64)
def get_synced_lyrics(title: str, artists: str) -> tuple[tuple[float, str], ...]:
    """Return ((timestamp_seconds, line), ...) for a track, or () if unavailable.

    Best-effort and silent: karaoke is a nice-to-have overlay, never worth
    surfacing a network/lookup error over.
    """
    query = f"{title} {artists}".strip()
    if not query:
        return ()
    try:
        import syncedlyrics
        lrc = syncedlyrics.search(query, synced_only=True)
    except Exception:
        return ()
    if not lrc:
        return ()
    return tuple(_parse_lrc(lrc))


def current_line(lines: tuple[tuple[float, str], ...], position: float) -> str:
    """Return the lyric line active at `position` seconds, or "" if none yet."""
    active = ""
    for timestamp, text in lines:
        if timestamp > position:
            break
        active = text
    return active
