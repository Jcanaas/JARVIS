"""Torrent magnet link search via torlink.

Uses torlink CLI (https://github.com/baairon/torlink) to search torrents
across multiple sources. Returns magnet links for streaming via peerflix.mov.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


class TorrentSearchError(RuntimeError):
    """Raised when torrent search fails."""


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


def _locate_torlink() -> str:
    """Find torlink executable in PATH."""
    torlink = shutil.which("torlink")
    if torlink:
        return torlink
    raise TorrentSearchError(
        "torlink not found. Install: npm install -g @baairon/torlink"
    )


def search(query: str, kind: str = "movie", limit: int = 10) -> list[Torrent]:
    """Search for torrents using torlink CLI.

    Args:
        query: Movie/TV title
        kind: "movie" | "tv"
        limit: Max results

    Returns:
        List of Torrent objects with magnet links

    Raises:
        TorrentSearchError: If search fails or no results found.
    """
    query = query.strip()
    if not query:
        raise TorrentSearchError("Empty search query.")

    try:
        torlink_exe = _locate_torlink()
    except TorrentSearchError:
        raise

    try:
        # Run: torlink search <query> --json --limit <limit>
        result = subprocess.run(
            [torlink_exe, "search", query, "--json", "--limit", str(limit + 5)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise TorrentSearchError(f"torlink failed: {result.stderr or result.stdout}")

        data = json.loads(result.stdout)
        if not data or not isinstance(data, list):
            raise TorrentSearchError(f"No torrents found for '{query}'.")

        torrents: list[Torrent] = []
        for item in data[:limit]:
            try:
                # torlink returns: name, magnet, seeders, leechers, etc.
                torrent = Torrent(
                    title=item.get("name", "Unknown"),
                    magnet=item.get("magnet", ""),
                    seeders=int(item.get("seeders", 0)) if item.get("seeders") else 0,
                    leechers=int(item.get("leechers", 0)) if item.get("leechers") else 0,
                    size=item.get("size", ""),
                )
                if torrent.magnet:
                    torrents.append(torrent)
            except (KeyError, ValueError, TypeError):
                continue

        if not torrents:
            raise TorrentSearchError(f"No valid torrents found for '{query}'.")

        # Sort by seeders descending
        torrents.sort(key=lambda t: t.seeders, reverse=True)
        return torrents[:limit]

    except json.JSONDecodeError as exc:
        raise TorrentSearchError(f"Failed to parse torlink results: {exc}") from exc
    except subprocess.TimeoutExpired:
        raise TorrentSearchError("torlink search timeout.") from None
    except Exception as exc:
        raise TorrentSearchError(f"torlink error: {exc}") from exc


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
