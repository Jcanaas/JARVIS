"""Torrent magnet link search.

Finds magnet links for movies/TV shows via 1337x tracker. Returns metadata
(title, seeders, leechers, upload date) and magnet URIs for playback with
peerflix or other torrent clients.

Note: 1337x domain may change periodically (DDoS/blocking). Falls back to
secondary domains if primary is unavailable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

_TIMEOUT = 15
_DOMAINS = [
    "https://1337x.to",
    "https://1337x.ws",
    "https://1337x.st",
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


def _get_working_domain() -> Optional[str]:
    """Find a working 1337x domain by trying a HEAD request."""
    for domain in _DOMAINS:
        try:
            r = requests.head(domain, timeout=5, allow_redirects=True)
            if r.status_code < 400:
                return domain
        except requests.RequestException:
            pass
    return None


def _fetch_search_page(query: str) -> str:
    """Fetch 1337x search results HTML. Raise TorrentSearchError if all domains down."""
    domain = _get_working_domain()
    if not domain:
        raise TorrentSearchError(
            "1337x is unreachable. The site may be down or blocked. Try again later."
        )

    url = f"{domain}/search/{query.replace(' ', '-')}/1/"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as exc:
        raise TorrentSearchError(f"Failed to search 1337x: {exc}") from exc


def search(query: str, kind: str = "movie", limit: int = 10) -> list[Torrent]:
    """Search 1337x for torrents.

    Args:
        query: Movie/TV title
        kind: "movie" | "tv" (affects quality/seed filtering)
        limit: Max results

    Returns:
        List of Torrent objects with magnet links

    Raises:
        TorrentSearchError: If search fails or no results found.
    """
    query = query.strip()
    if not query:
        raise TorrentSearchError("Empty search query.")

    html = _fetch_search_page(query)
    soup = BeautifulSoup(html, "html.parser")

    # 1337x structure: <a> tags in a table with href like /torrent/12345/title/
    torrents: list[Torrent] = []
    for row in soup.select("table tbody tr"):
        try:
            cells = row.select("td")
            if len(cells) < 5:
                continue

            # Extract title and torrent ID from the link
            name_elem = cells[0].select_one("a[href*='/torrent/']")
            if not name_elem:
                continue
            title = name_elem.get_text(strip=True)
            href = name_elem.get("href", "")

            # Extract /torrent/{id}/ to fetch magnet later
            match = re.search(r"/torrent/(\d+)/", href)
            if not match:
                continue
            torrent_id = match.group(1)

            # Parse seeders/leechers (appear in the row)
            seeders_text = cells[1].get_text(strip=True) if len(cells) > 1 else "0"
            leechers_text = cells[2].get_text(strip=True) if len(cells) > 2 else "0"
            try:
                seeders = int(seeders_text)
                leechers = int(leechers_text)
            except ValueError:
                seeders = leechers = 0

            # Fetch the torrent page to extract magnet link
            magnet = _get_magnet(torrent_id)
            if not magnet:
                continue

            torrents.append(
                Torrent(
                    title=title,
                    magnet=magnet,
                    seeders=seeders,
                    leechers=leechers,
                )
            )
            if len(torrents) >= limit:
                break
        except (IndexError, ValueError, AttributeError):
            continue

    if not torrents:
        raise TorrentSearchError(f"No torrents found for '{query}' on 1337x.")

    # Sort by seeders descending
    torrents.sort(key=lambda t: t.seeders, reverse=True)
    return torrents[:limit]


def _get_magnet(torrent_id: str) -> Optional[str]:
    """Fetch magnet link from a 1337x torrent page by ID."""
    domain = _get_working_domain()
    if not domain:
        return None

    url = f"{domain}/torrent/{torrent_id}/"
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Look for <a href="magnet:..."> tag
        magnet_elem = soup.select_one("a[href^='magnet:']")
        if magnet_elem:
            return magnet_elem.get("href", "")
    except requests.RequestException:
        pass
    return None


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
