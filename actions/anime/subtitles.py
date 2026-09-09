"""External subtitles for the browser <video> engine, as WebVTT.

Playback is the browser <video> tag, whose <track> element only accepts
WebVTT — it will not read the .srt that actions.stremio_subs downloads. This
module is that one format step: fetch the same .srt, convert to VTT, done.

Kept deliberately tiny — SRT and WebVTT share the same cue structure
(index, timestamp range, text lines, blank line), so the "conversion" is
swapping the comma decimal separator for a dot and prepending the WEBVTT
header. No third-party dependency earns its place for that.
"""
from __future__ import annotations

import re

from actions import stremio_subs

_TIMESTAMP_RE = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")


def srt_to_vtt(srt_text: str) -> str:
    """Convert SRT subtitle text to WebVTT text."""
    body = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    body = _TIMESTAMP_RE.sub(r"\1.\2", body)
    return "WEBVTT\n\n" + body.strip() + "\n"


def get_vtt_by_kitsu(kitsu_id: str, episode: int = 0, language: str = "es") -> str:
    """Fetch, download and convert an external subtitle to WebVTT text.

    Raises stremio_subs.StremioSubsError when no subtitle is found or the
    download fails — callers treat that as "no subtitles available", not a
    hard failure of playback.
    """
    path = stremio_subs.fetch_subtitle_by_kitsu(kitsu_id, episode=episode,
                                                language=language)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        srt_text = f.read()
    return srt_to_vtt(srt_text)
