"""Torrent magnet link search.

Finds magnet links for movies/TV shows via multiple trackers:
- 1337x: primary tracker with domain failover
- TorrentGalaxy: fallback tracker if 1337x unavailable

Returns metadata (title, seeders, leechers) and magnet URIs for playback
with peerflix or other torrent clients.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

_TIMEOUT = 15

# 1337x domains (primary tracker)
_1337X_DOMAINS = [
    "https://1337x.to",
    "https://1337x.ws",
    "https://1337x.st",
    "https://1337x.io",
    "https://1337x.se",
    "https://x1337x.ws",
    "https://1337x.unblocked",
]

# TorrentGalaxy domains (fallback tracker)
_TORRENTGALAXY_DOMAINS = [
    "https://torrentgalaxy.to",
    "https://torrentgalaxy.org",
    "https://torrentgalaxy.mx",
]


class TorrentSearchError(RuntimeError):
    """Raised when magnet search fails (no results, all domains down, etc)."""


@dataclass
class Torrent:
    """Torrent metadata and magnet link."""

    title: str
    magnet: str
    seeders: int = 0
    leechers: int = 0
    upload_date: str = ""
    size: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "magnet": self.magnet,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "upload_date": self.upload_date,
            "size": self.size,
        }


def _get_working_domain(domains: list[str]) -> Optional[str]:
    """Find a working domain by trying a HEAD request."""
    for domain in domains:
        try:
            r = requests.head(domain, timeout=5, allow_redirects=True)
            if r.status_code < 400:
                return domain
        except requests.RequestException:
            pass
    return None


def _search_1337x(query: str, limit: int) -> list[Torrent]:
    """Search 1337x tracker for torrents."""
    domain = _get_working_domain(_1337X_DOMAINS)
    if not domain:
        raise TorrentSearchError("1337x unreachable")

    url = f"{domain}/search/{query.replace(' ', '-')}/1/"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        html = r.text
    except requests.RequestException as exc:
        raise TorrentSearchError(f"1337x search failed: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")
    torrents: list[Torrent] = []

    for row in soup.select("table tbody tr"):
        try:
            cells = row.select("td")
            if len(cells) < 5:
                continue

            name_elem = cells[0].select_one("a[href*='/torrent/']")
            if not name_elem:
                continue
            title = name_elem.get_text(strip=True)
            href = name_elem.get("href", "")

            match = re.search(r"/torrent/(\d+)/", href)
            if not match:
                continue
            torrent_id = match.group(1)

            seeders_text = cells[1].get_text(strip=True) if len(cells) > 1 else "0"
            leechers_text = cells[2].get_text(strip=True) if len(cells) > 2 else "0"
            try:
                seeders = int(seeders_text)
                leechers = int(leechers_text)
            except ValueError:
                seeders = leechers = 0

            magnet = _get_magnet_1337x(torrent_id, domain)
            if not magnet:
                continue

            torrents.append(Torrent(title=title, magnet=magnet, seeders=seeders, leechers=leechers))
            if len(torrents) >= limit:
                break
        except (IndexError, ValueError, AttributeError):
            continue

    if not torrents:
        raise TorrentSearchError("No results on 1337x")

    torrents.sort(key=lambda t: t.seeders, reverse=True)
    return torrents[:limit]


def _get_magnet_1337x(torrent_id: str, domain: str) -> Optional[str]:
    """Fetch magnet link from 1337x torrent page."""
    url = f"{domain}/torrent/{torrent_id}/"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        magnet_elem = soup.select_one("a[href^='magnet:']")
        if magnet_elem:
            return magnet_elem.get("href", "")
    except requests.RequestException:
        pass
    return None


def _search_torrentgalaxy(query: str, limit: int) -> list[Torrent]:
    """Search TorrentGalaxy tracker for torrents."""
    domain = _get_working_domain(_TORRENTGALAXY_DOMAINS)
    if not domain:
        raise TorrentSearchError("TorrentGalaxy unreachable")

    url = f"{domain}/torrents.php?search={query.replace(' ', '+')}&sort=seeders&order=desc"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        html = r.text
    except requests.RequestException as exc:
        raise TorrentSearchError(f"TorrentGalaxy search failed: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")
    torrents: list[Torrent] = []

    # TorrentGalaxy structure: rows with data-id attribute
    for row in soup.select("tr[data-id]"):
        try:
            # Extract title
            title_elem = row.select_one("a.txlight")
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)

            # Extract seeders/leechers
            cells = row.select("td")
            if len(cells) < 6:
                continue

            try:
                seeders = int(cells[4].get_text(strip=True))
                leechers = int(cells[5].get_text(strip=True))
            except (ValueError, IndexError):
                seeders = leechers = 0

            # Extract magnet from link
            magnet_elem = row.select_one("a[href^='magnet:']")
            if not magnet_elem:
                continue
            magnet = magnet_elem.get("href", "")

            torrents.append(Torrent(title=title, magnet=magnet, seeders=seeders, leechers=leechers))
            if len(torrents) >= limit:
                break
        except (IndexError, AttributeError):
            continue

    if not torrents:
        raise TorrentSearchError("No results on TorrentGalaxy")

    torrents.sort(key=lambda t: t.seeders, reverse=True)
    return torrents[:limit]


def search(query: str, kind: str = "movie", limit: int = 10) -> list[Torrent]:
    """Search for torrents on multiple trackers.

    Tries 1337x first, falls back to TorrentGalaxy if unavailable.

    Args:
        query: Movie/TV title
        kind: "movie" | "tv"
        limit: Max results

    Returns:
        List of Torrent objects with magnet links

    Raises:
        TorrentSearchError: If all trackers fail or no results found.
    """
    query = query.strip()
    if not query:
        raise TorrentSearchError("Empty search query.")

    # Try 1337x first
    try:
        return _search_1337x(query, limit)
    except TorrentSearchError:
        pass

    # Fall back to TorrentGalaxy
    try:
        return _search_torrentgalaxy(query, limit)
    except TorrentSearchError:
        pass

    # Both trackers failed
    raise TorrentSearchError(
        "All torrent trackers unreachable. Try again later or check your connection."
    )


def search_action(parameters: dict) -> str:
    """Voice/agent entry point for torrent search.

    parameters:
        query: movie/TV title
        kind: "movie" | "tv"
    """
    query = (parameters.get("query") or "").strip()
    kind = (parameters.get("kind") or "movie").strip().lower()

    if not query:
        return "¿Qué película o serie buscas?"

    try:
        torrents = search(query, kind=kind, limit=8)
        lines = [f"Encontré {len(torrents)} torrents:"]
        for i, t in enumerate(torrents, 1):
            seed_str = f" 📤{t.seeders}" if t.seeders else ""
            lines.append(f"{i}. {t.title}{seed_str}")
        return "\n".join(lines)
    except TorrentSearchError as exc:
        return f"Error buscando torrents: {exc}"
