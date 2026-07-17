"""Spanish-focused torrent source via the Peerflix Stremio addon (peerflix.mov).

Peerflix aggregates Spanish (Spain) trackers — DonTorrent, MejorTorrent,
Wolfmax4k and friends — and exposes them through the Stremio addon protocol,
keyed by IMDb id. Its streams are almost entirely Castilian, making it the best
source for Spanish audio. Unlike Torrentio it returns structured fields
(seed / sizebytes / language / quality), so parsing is straightforward.

API: https://peerflix.mov/stream/<type>/<imdb_id>.json  (type: movie | series)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import requests

from actions.doh import enable_for

# Route Peerflix through DNS-over-HTTPS in case the ISP blocks it too.
enable_for("peerflix.mov")

_BASE = "https://peerflix.mov"
_TIMEOUT = 12

_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://tracker.dler.org:6969/announce",
]

_SPANISH_RE = re.compile(r"castellano|español|espanol|🇪🇸|latino", re.IGNORECASE)


class PeerflixAddonError(RuntimeError):
    """Raised when the Peerflix lookup fails."""


@dataclass
class Stream:
    """A Peerflix stream result."""

    title: str
    magnet: str
    seeders: int = 0
    size: str = ""
    provider: str = "Peerflix"
    spanish: bool = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "magnet": self.magnet,
            "seeders": self.seeders,
            "size": self.size,
            "provider": self.provider,
            "spanish": self.spanish,
        }


def _fmt_size(num_bytes) -> str:
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}".rstrip("0").rstrip(".") + ("" if unit == "B" else "")
        n /= 1024
    return f"{n:.2f} PB"


def _build_magnet(info_hash: str, name: str) -> str:
    trackers = "".join(f"&tr={quote(t)}" for t in _TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}{trackers}"


def _parse_stream(item: dict) -> Stream | None:
    info_hash = item.get("infoHash")
    if not info_hash:
        return None

    desc = item.get("description") or item.get("title") or ""
    name = next((ln for ln in desc.split("\n") if ln.strip()), "Unknown")

    # Prefer the structured fields; fall back to scraping the description.
    seeders = item.get("seed")
    if seeders is None:
        m = re.search(r"👤\s*(\d+)", desc)
        seeders = int(m.group(1)) if m else 0
    try:
        seeders = int(seeders)
    except (TypeError, ValueError):
        seeders = 0

    size = _fmt_size(item.get("sizebytes"))
    if not size:
        m = re.search(r"💾\s*([\d.]+\s*[KMGT]B)", desc)
        size = m.group(1) if m else ""

    blob = f"{item.get('name', '')} {item.get('language', '')} {desc}"
    return Stream(
        title=name,
        magnet=_build_magnet(info_hash, name),
        seeders=seeders,
        size=size,
        spanish=bool(_SPANISH_RE.search(blob)),
    )


def search(imdb_id: str, kind: str = "movie", season: int = 0, episode: int = 0,
           spanish: bool = True, limit: int = 15) -> list[Stream]:
    """Fetch Spanish-focused torrent streams for an IMDb id via Peerflix.

    Raises:
        PeerflixAddonError: on missing id, network failure, or no results.
    """
    if not imdb_id:
        raise PeerflixAddonError("No IMDb id provided.")

    stream_type = "series" if kind == "tv" else "movie"
    video_id = imdb_id
    if stream_type == "series" and season and episode:
        video_id = f"{imdb_id}:{season}:{episode}"

    url = f"{_BASE}/stream/{stream_type}/{video_id}.json"

    try:
        r = requests.get(url, timeout=_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise PeerflixAddonError(f"Peerflix request failed: {exc}") from exc
    except ValueError as exc:
        raise PeerflixAddonError(f"Invalid Peerflix response: {exc}") from exc

    streams: list[Stream] = []
    for item in data.get("streams", []):
        s = _parse_stream(item)
        if s:
            streams.append(s)

    if not streams:
        raise PeerflixAddonError(f"No Peerflix streams for '{imdb_id}'.")

    if spanish:
        streams.sort(key=lambda s: (s.spanish, s.seeders), reverse=True)
    else:
        streams.sort(key=lambda s: s.seeders, reverse=True)

    return streams[:limit]
