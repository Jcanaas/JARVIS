"""Linear retiming of SubRip (.srt) subtitles.

A subtitle whose desync *grows* over time is not a constant offset — it was
authored against a different frame rate than the video being played (e.g. a
25 fps PAL subtitle over a 23.976 fps release). VLC's spu-delay only shifts by a
constant, so it can't fix a drift that accumulates. The correct fix is to
rescale every timestamp: ``new_t = t * scale + offset_seconds``.

Two entry points:
  * ``retime_fps``   — scale from a source fps to a target fps (presets).
  * ``retime_scale`` — scale by an explicit multiplier (+ optional offset).

Both read an .srt, rewrite its timestamps, and save to a new file next to the
original (``<name>.retimed.srt``) so the source stays intact for re-tries.
"""
from __future__ import annotations

import re
from pathlib import Path

# HH:MM:SS,mmm --> HH:MM:SS,mmm  (comma or dot decimal separator both seen)
_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


class SubtitleRetimeError(RuntimeError):
    """Raised when the subtitle can't be read or contains no timestamps."""


def _to_ms(h: str, m: str, s: str, ms: str) -> int:
    # ms group may be 1-3 digits; pad so "5" -> 500 ms, "05" -> 050 ms.
    ms_i = int((ms + "000")[:3])
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + ms_i


def _fmt(total_ms: int) -> str:
    if total_ms < 0:
        total_ms = 0
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _read_text(src: Path) -> str:
    raw = src.read_bytes()
    # Strip UTF-8 BOM; fall back to latin-1 for legacy Windows-1252 subs.
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def retime_scale(src_path: str, scale: float, offset_seconds: float = 0.0,
                 dest_path: str | None = None) -> str:
    """Rewrite every timestamp as ``t * scale + offset_seconds``.

    Args:
        src_path: Path to the source .srt.
        scale: Multiplier applied to each timestamp (1.0 = unchanged).
        offset_seconds: Constant shift added after scaling (can be negative).
        dest_path: Output path; defaults to ``<name>.retimed.srt``.

    Returns:
        Absolute path to the written .srt.

    Raises:
        SubtitleRetimeError: If the file is unreadable or has no timestamps.
    """
    src = Path(src_path)
    if not src.exists():
        raise SubtitleRetimeError(f"Subtítulo no encontrado: {src_path}")

    text = _read_text(src)
    offset_ms = int(round(offset_seconds * 1000))
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        start = int(round(_to_ms(*m.group(1, 2, 3, 4)) * scale)) + offset_ms
        end = int(round(_to_ms(*m.group(5, 6, 7, 8)) * scale)) + offset_ms
        return f"{_fmt(start)} --> {_fmt(end)}"

    out = _TIME_RE.sub(_sub, text)
    if count == 0:
        raise SubtitleRetimeError("El archivo no contiene marcas de tiempo SRT.")

    dest = Path(dest_path) if dest_path else src.with_suffix(".retimed.srt")
    dest.write_text(out, encoding="utf-8")
    return str(dest)


def retime_fps(src_path: str, src_fps: float, dst_fps: float,
               offset_seconds: float = 0.0, dest_path: str | None = None) -> str:
    """Rescale a subtitle authored at ``src_fps`` to play at ``dst_fps``.

    A cue that sits on frame N moves from ``N/src_fps`` to ``N/dst_fps``, so the
    timeline scale is ``src_fps / dst_fps``.
    """
    if src_fps <= 0 or dst_fps <= 0:
        raise SubtitleRetimeError("FPS inválido.")
    return retime_scale(src_path, src_fps / dst_fps, offset_seconds, dest_path)
