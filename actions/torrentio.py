"""Torrent source aggregation via the Torrentio API (Stremio addon).

Torrentio scrapes many trackers — including Spanish-only sites that sit behind
Cloudflare (MejorTorrent, Wolfmax4k, Cinecalidad, EliteTorrent) — and exposes
them as a simple JSON API keyed by IMDb id. We use it as an extra source so the
movie player gets genuine Castilian/Spanish releases we can't scrape directly.

API shape:
  https://torrentio.strem.fun/<config>/stream/<type>/<imdb_id>.json
  type: "movie" | "series"; for series append ":<season>:<episode>"
Each stream carries an infoHash (→ magnet), a title with "👤 <seeders>",
"💾 <size>" and "⚙️ <provider>", and language flag emojis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from actions.doh import enable_for

# Route Torrentio through DNS-over-HTTPS in case the ISP blocks it too.
enable_for("strem.fun")

_BASE = "https://torrentio.strem.fun"
_TIMEOUT = 12

# Spanish-focused providers (the whole point of adding Torrentio here) plus the
# big international ones so we still get high-seed English releases.
_PROVIDERS = "mejortorrent,wolfmax4k,cinecalidad,elitetorrent,yts,eztv,thepiratebay,1337x"
_ANIME_PROVIDERS = "nyaa,eztv,1337x"

_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.stealth.si:80/announce",
]

# Torrentio tags language with a flag emoji, which is reliable; "dual" alone is
# not (e.g. "Dual Japanese/English"), so it's excluded to avoid false positives.
_SPANISH_RE = re.compile(r"castellano|español|espanol|🇪🇸|latino", re.IGNORECASE)


class TorrentioError(RuntimeError):
    """Raised when the Torrentio lookup fails."""


@dataclass
class Stream:
    """A Torrentio stream result."""

    title: str
    magnet: str
    seeders: int = 0
    size: str = ""
    provider: str = ""
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


def _build_magnet(info_hash: str, name: str) -> str:
    from urllib.parse import quote
    trackers = "".join(f"&tr={quote(t)}" for t in _TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}{trackers}"


def _parse_stream(item: dict) -> Stream | None:
    info_hash = item.get("infoHash")
    if not info_hash:
        return None
    raw = item.get("title", "")
    lines = [ln for ln in raw.split("\n") if ln.strip()]
    name = lines[0] if lines else "Unknown"

    seeders = 0
    m = re.search(r"👤\s*(\d+)", raw)
    if m:
        seeders = int(m.group(1))
    size = ""
    m = re.search(r"💾\s*([\d.]+\s*[KMGT]B)", raw)
    if m:
        size = m.group(1)
    provider = ""
    m = re.search(r"⚙️\s*([^\n]+)", raw)
    if m:
        provider = m.group(1).strip()

    return Stream(
        title=name,
        magnet=_build_magnet(info_hash, name),
        seeders=seeders,
        size=size,
        provider=provider,
        spanish=bool(_SPANISH_RE.search(raw)),
    )


def search(imdb_id: str, kind: str = "movie", season: int = 0, episode: int = 0,
           spanish: bool = True, limit: int = 15) -> list[Stream]:
    """Fetch torrent streams for an IMDb id via Torrentio.

    Args:
        imdb_id: IMDb id like "tt0133093"
        kind: "movie" | "tv"
        season/episode: for TV episodes
        spanish: rank Castilian/Spanish releases first
        limit: max results

    Returns:
        List of Stream objects.

    Raises:
        TorrentioError: on missing id, network failure, or no results.
    """
    if not imdb_id:
        raise TorrentioError("No IMDb id provided.")

    stream_type = "series" if kind == "tv" else "movie"
    video_id = imdb_id
    if stream_type == "series" and season and episode:
        video_id = f"{imdb_id}:{season}:{episode}"

    url = f"{_BASE}/providers={_PROVIDERS}/stream/{stream_type}/{video_id}.json"

    try:
        # Pass the config path pre-formatted so requests doesn't percent-encode
        # the commas/pipe Torrentio expects literally.
        r = requests.get(url, timeout=_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise TorrentioError(f"Torrentio request failed: {exc}") from exc
    except ValueError as exc:
        raise TorrentioError(f"Invalid Torrentio response: {exc}") from exc

    streams: list[Stream] = []
    for item in data.get("streams", []):
        s = _parse_stream(item)
        if s:
            streams.append(s)

    if not streams:
        raise TorrentioError(f"No Torrentio streams for '{imdb_id}'.")

    if spanish:
        streams.sort(key=lambda s: (s.spanish, s.seeders), reverse=True)
    else:
        streams.sort(key=lambda s: s.seeders, reverse=True)

    return streams[:limit]


def search_by_id(video_id: str, stream_type: str = "series",
                 limit: int = 15) -> list[Stream]:
    """Fetch anime streams for a ready-made Stremio video id.

    `video_id` is passed straight into the stream path, so it accepts Kitsu ids
    with absolute episode numbering ("kitsu:12:1044") — the format Anime Kitsu
    produces and Torrentio indexes natively. Use stream_type="series" for
    episodes ("kitsu:<id>:<ep>") and "movie" for anime films ("kitsu:<id>").
    No providers= filter, so Torrentio's default config (which includes NyaaSi,
    the dominant anime tracker) is used.
    """
    if not video_id:
        raise TorrentioError("No video id provided.")

    stream_type = "movie" if stream_type == "movie" else "series"
    url = f"{_BASE}/stream/{stream_type}/{video_id}.json"

    try:
        r = requests.get(url, timeout=_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise TorrentioError(f"Torrentio request failed: {exc}") from exc
    except ValueError as exc:
        raise TorrentioError(f"Invalid Torrentio response: {exc}") from exc

    streams = [s for item in data.get("streams", []) if (s := _parse_stream(item))]
    if not streams:
        raise TorrentioError(f"No Torrentio streams for '{video_id}'.")

    streams.sort(key=lambda s: s.seeders, reverse=True)
    return streams[:limit]


def search_anime(imdb_id: str, kind: str = "tv", season: int = 0, episode: int = 0,
                 limit: int = 15) -> list[Stream]:
    """Fetch anime torrent streams via Torrentio (default config, NyaaSi source).

    Torrentio's NyaaSi provider is only active when NO provider filter is set.
    Filtering by providers=nyaa returns 0 results — the internal name differs.
    """
    if not imdb_id:
        raise TorrentioError("No IMDb id provided.")

    stream_type = "series" if kind == "tv" else "movie"
    video_id = imdb_id
    if stream_type == "series" and season and episode:
        video_id = f"{imdb_id}:{season}:{episode}"

    # No providers= filter: Torrentio's default config includes NyaaSi which is
    # the dominant anime source. Adding a providers= param kills NyaaSi results.
    url = f"{_BASE}/stream/{stream_type}/{video_id}.json"

    try:
        r = requests.get(url, timeout=_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise TorrentioError(f"Torrentio anime request failed: {exc}") from exc
    except ValueError as exc:
        raise TorrentioError(f"Invalid Torrentio response: {exc}") from exc

    streams = [s for item in data.get("streams", []) if (s := _parse_stream(item))]
    if not streams:
        raise TorrentioError(f"No anime streams for '{imdb_id}'.")

    streams.sort(key=lambda s: s.seeders, reverse=True)
    return streams[:limit]
