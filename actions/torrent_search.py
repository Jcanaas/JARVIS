"""Torrent magnet link search via a vendored torlink search script.

Runs actions/vendor/torlink/search.mjs (a search-only subset of torlink,
https://github.com/baairon/torlink, MIT) with Node.js to query YTS, The
Pirate Bay and 1337x, and returns normalized magnet links.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from actions.paths import resource

_TIMEOUT = 12  # search.mjs self-caps at ~8s; this leaves margin for startup
_SEARCH_SCRIPT = resource("actions", "vendor", "torlink", "search.mjs")


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
    spanish: bool = False  # Castilian/Spanish audio detected in the title
    provider: str = ""     # source label (YTS, 1337x, Peerflix, MejorTorrent…)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "magnet": self.magnet,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "upload_date": self.upload_date,
            "size": self.size,
            "spanish": self.spanish,
            "provider": self.provider,
        }


def _locate_node() -> str:
    """Find the Node.js executable in PATH."""
    node = shutil.which("node")
    if node:
        return node
    raise TorrentSearchError(
        "Node.js not found. Install it from https://nodejs.org to enable torrent search."
    )


def search(query: str, kind: str = "movie", limit: int = 10,
           spanish: bool = True) -> list[Torrent]:
    """Search for torrents via the vendored torlink script.

    Args:
        query: Movie/TV title
        kind: "movie" | "tv"
        limit: Max results
        spanish: Prioritise Castilian/Spanish releases (extra tracker passes
            with a "castellano" hint, Spanish results ranked first)

    Returns:
        List of Torrent objects with magnet links.

    Raises:
        TorrentSearchError: If Node.js is missing, the search fails, or no
            results are found.
    """
    query = query.strip()
    if not query:
        raise TorrentSearchError("Empty search query.")

    node = _locate_node()
    kind = "tv" if kind == "tv" else "anime" if kind == "anime" else "movie"

    cmd = [node, str(_SEARCH_SCRIPT), "search", query,
           "--kind", kind, "--limit", str(limit), "--json"]
    if spanish:
        cmd.append("--spanish")

    try:
        # The Node script prints UTF-8 JSON (anime titles contain Japanese and
        # other non-Latin-1 characters). Force UTF-8 decoding — text=True would
        # default to cp1252 on Windows and raise UnicodeDecodeError on byte 0x81.
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise TorrentSearchError("Torrent search timed out.") from None

    if result.returncode != 0:
        raise TorrentSearchError(
            f"Torrent search failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TorrentSearchError(f"Failed to parse search results: {exc}") from exc

    if not data:
        raise TorrentSearchError(f"No torrents found for '{query}'.")

    torrents = [
        Torrent(
            title=item.get("name", "Unknown"),
            magnet=item.get("magnet", ""),
            seeders=int(item.get("seeders") or 0),
            leechers=int(item.get("leechers") or 0),
            size=item.get("size", ""),
            spanish=bool(item.get("spanish", False)),
            provider=item.get("source", ""),
        )
        for item in data
        if item.get("magnet")
    ]

    if not torrents:
        raise TorrentSearchError(f"No valid torrents found for '{query}'.")

    # The script already ranks (Spanish first when requested, then seeders); keep
    # that order rather than re-sorting purely by seeders.
    return torrents[:limit]


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
