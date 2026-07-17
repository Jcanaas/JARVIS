"""TV mode — live IPTV channel lineups from the iptv-org project.

Playlists come from https://github.com/iptv-org/iptv, served as per-country
M3U files from iptv-org.github.io. Fetched playlists are cached on disk
(MEMORY_DIR/tv_cache/<country>.m3u) with a TTL so entering the mode is
instant and a network hiccup still shows the last known lineup.

Each ``#EXTINF`` entry may be followed by ``#EXTVLCOPT:`` lines (user-agent,
referrer some broadcasters require); those are kept per channel and passed to
libVLC as media options when the stream is opened.
"""
from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from actions.paths import memory_path

_PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/{code}.m3u"
# Spain: iptv-org's RTVE links point at the DRM (SAMPLE-AES) masters, which
# libVLC cannot decrypt (black picture / "audio only"). TDTChannels is a
# daily-maintained Spanish list with the clear streams, so for "es" its URLs
# take precedence over iptv-org's on name match.
_TDT_URL = "https://www.tdtchannels.com/lists/tv.m3u8"
_CACHE_DIR = memory_path("tv_cache")
_CACHE_TTL = 24 * 3600  # seconds

# Curated country selection for the combo (code -> label). iptv-org publishes
# one playlist per ISO 3166-1 alpha-2 code; add more here when needed.
COUNTRIES: dict[str, str] = {
    "es": "España",
    "us": "Estados Unidos",
    "uk": "Reino Unido",
    "fr": "Francia",
    "de": "Alemania",
    "it": "Italia",
    "pt": "Portugal",
    "mx": "México",
    "ar": "Argentina",
    "jp": "Japón",
}

DEFAULT_COUNTRY = "es"

_ATTR_RE = re.compile(r'([a-zA-Z0-9-]+)="([^"]*)"')


@dataclass
class Channel:
    name: str
    url: str
    logo: str = ""
    group: str = ""
    tvg_id: str = ""
    vlc_opts: list[str] = field(default_factory=list)


def _parse_m3u(text: str) -> list[Channel]:
    channels: list[Channel] = []
    current: Channel | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(_ATTR_RE.findall(line))
            name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            current = Channel(
                name=name or attrs.get("tvg-id", "") or "Canal",
                url="",
                logo=attrs.get("tvg-logo", ""),
                group=attrs.get("group-title", ""),
                tvg_id=attrs.get("tvg-id", ""),
            )
        elif line.startswith("#EXTVLCOPT:"):
            if current is not None:
                opt = line[len("#EXTVLCOPT:"):].strip()
                if opt:
                    current.vlc_opts.append(":" + opt.lstrip(":"))
        elif line.startswith("#"):
            continue
        elif current is not None:
            current.url = line
            channels.append(current)
            current = None
    return channels


def _cache_file(stem: str) -> Path:
    return _CACHE_DIR / f"{stem}.m3u"


def _read_cache(stem: str, max_age: float | None) -> str | None:
    f = _cache_file(stem)
    try:
        if not f.is_file():
            return None
        if max_age is not None and (time.time() - f.stat().st_mtime) > max_age:
            return None
        return f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _fetch_playlist(url: str, cache_stem: str, force: bool,
                    timeout: int) -> str:
    """Playlist text, from disk cache when fresh; stale cache on network
    failure rather than raising, so the mode keeps working offline."""
    if not force:
        cached = _read_cache(cache_stem, _CACHE_TTL)
        if cached:
            return cached
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        text = urllib.request.urlopen(req, timeout=timeout).read().decode(
            "utf-8", errors="replace")
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _cache_file(cache_stem).write_text(text, encoding="utf-8")
        except Exception:
            pass
        return text
    except Exception:
        stale = _read_cache(cache_stem, None)
        if stale:
            return stale
        raise


def _normalize_name(name: str) -> str:
    """Channel name key for cross-list matching: drops '(720p)'-style and
    '[Geo-blocked]'-style suffixes, collapses whitespace, lowercases."""
    name = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", name or "")
    return re.sub(r"\s+", " ", name).strip().lower()


def _merge_overrides(base: list[Channel], extra: list[Channel]) -> list[Channel]:
    """Overlay a second playlist: on a name match the extra list's stream URL
    (and VLC options) replace the base one; unmatched extras are appended.
    Extra entries with templated ad-insertion URLs ('[DEVICE_ID]'…) or
    duplicate names are dropped."""
    by_name = {_normalize_name(c.name): c for c in base}
    seen: set[str] = set()
    extras: list[Channel] = []
    for ch in extra:
        if "[" in ch.url:
            continue
        key = _normalize_name(ch.name)
        if not key or key in seen:
            continue
        seen.add(key)
        hit = by_name.get(key)
        if hit is not None:
            hit.url = ch.url
            hit.vlc_opts = list(ch.vlc_opts)
            if not hit.logo:
                hit.logo = ch.logo
        else:
            extras.append(ch)
    return base + extras


def fetch_channels(country: str = DEFAULT_COUNTRY, force: bool = False,
                   timeout: int = 20) -> list[Channel]:
    """Channel lineup for a country (iptv-org, disk-cached).

    For Spain the TDTChannels list is overlaid on top (clear RTVE streams
    instead of iptv-org's DRM ones); if it can't be fetched the iptv-org
    lineup is returned as-is.
    """
    country = (country or DEFAULT_COUNTRY).lower()
    channels = _parse_m3u(_fetch_playlist(
        _PLAYLIST_URL.format(code=country), country, force, timeout))
    if country == "es":
        try:
            tdt = _parse_m3u(_fetch_playlist(_TDT_URL, "es_tdt", force, timeout))
            channels = _merge_overrides(channels, tdt)
        except Exception:
            pass
    return channels


def channel_groups(channels: list[Channel]) -> list[str]:
    """Distinct group-title values, split on ';' (iptv-org concatenates
    multiple categories that way), sorted for the filter combo."""
    seen: set[str] = set()
    for ch in channels:
        for part in (ch.group or "").split(";"):
            part = part.strip()
            if part:
                seen.add(part)
    return sorted(seen)
