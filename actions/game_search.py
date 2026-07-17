"""Game repack search via a vendored torlink search script.

Runs actions/vendor/torlink/search.mjs (a search-only subset of torlink,
https://github.com/baairon/torlink, MIT) with Node.js to query FitGirl
Repacks (the only games source, mirroring torlink's own security stance —
games are the one category that can run code) and returns normalized magnet
links.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from actions.paths import find_node, resource

_TIMEOUT = 12  # search.mjs self-caps at ~8s; this leaves margin for startup
_SEARCH_SCRIPT = resource("actions", "vendor", "torlink", "search.mjs")


class GameSearchError(RuntimeError):
    """Raised when game repack search fails."""


@dataclass
class Repack:
    """Repack metadata and magnet link."""

    title: str
    magnet: str
    seeders: int = 0
    leechers: int = 0
    size: str = ""
    provider: str = "fitgirl"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "magnet": self.magnet,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "size": self.size,
            "provider": self.provider,
        }


def _locate_node() -> str:
    """Find the Node.js executable (bundled runtime first, then PATH)."""
    node = find_node()
    if node:
        return node
    raise GameSearchError(
        "Node.js not found. Install it from https://nodejs.org to enable game search."
    )


def search(query: str, limit: int = 10) -> list[Repack]:
    """Search FitGirl Repacks via the vendored torlink script.

    Args:
        query: Game title
        limit: Max results

    Returns:
        List of Repack objects with magnet links.

    Raises:
        GameSearchError: If Node.js is missing, the search fails, or no
            results are found.
    """
    query = query.strip()
    if not query:
        raise GameSearchError("Empty search query.")

    node = _locate_node()
    cmd = [node, str(_SEARCH_SCRIPT), "search", query,
           "--kind", "game", "--limit", str(limit), "--json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise GameSearchError("Game search timed out.") from None

    # search.mjs force-exits after writing stdout (process.exit(0)) to avoid
    # hanging on abandoned fetches; on Windows this occasionally races a libuv
    # cleanup assertion and reports a nonzero exit even though stdout already
    # holds valid JSON. Trust the payload over the exit code when it parses.
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        if result.returncode != 0:
            raise GameSearchError(
                f"Game search failed: {result.stderr.strip() or result.stdout.strip()}"
            ) from exc
        raise GameSearchError(f"Failed to parse search results: {exc}") from exc

    if not data:
        raise GameSearchError(f"No repacks found for '{query}'.")

    repacks = [
        Repack(
            title=item.get("name", "Unknown"),
            magnet=item.get("magnet", ""),
            seeders=int(item.get("seeders") or 0),
            leechers=int(item.get("leechers") or 0),
            size=item.get("size", ""),
            provider=item.get("source", "fitgirl"),
        )
        for item in data
        if item.get("magnet")
    ]

    if not repacks:
        raise GameSearchError(f"No valid repacks found for '{query}'.")

    return repacks[:limit]


def search_action(parameters: dict) -> str:
    """Voice/agent entry point for game repack search.

    parameters:
        query: game title
    """
    query = (parameters.get("query") or "").strip()

    if not query:
        return "¿Qué juego buscas?"

    try:
        repacks = search(query, limit=8)
        lines = [f"Encontré {len(repacks)} repacks:"]
        for i, r in enumerate(repacks, 1):
            size_str = f" ({r.size})" if r.size else ""
            lines.append(f"{i}. {r.title}{size_str}")
        return "\n".join(lines)
    except GameSearchError as exc:
        return f"Error buscando repacks: {exc}"
