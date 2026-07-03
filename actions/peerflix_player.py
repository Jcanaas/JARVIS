"""Peerflix-based torrent streaming.

Launches peerflix (npm package) to stream a torrent magnet link without
downloading the full file first. peerflix auto-opens in mpv by default.

Requires: peerflix installed globally or locally
  npm install -g peerflix
  or use npx: npx peerflix <magnet>
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


class PeerflixError(RuntimeError):
    """Raised when peerflix is unavailable or playback fails."""


def _locate_peerflix() -> str:
    """Find peerflix executable: global npm, local npx, or PATH."""
    # Try global npm install
    peerflix = shutil.which("peerflix")
    if peerflix:
        return peerflix

    # Try npx (comes with npm 5.2+)
    npx = shutil.which("npx")
    if npx:
        return "npx peerflix"  # Will be invoked as subprocess

    raise PeerflixError(
        "peerflix not found. Install with: npm install -g peerflix"
    )


def play(magnet: str, title: str = "") -> subprocess.Popen:
    """Stream a torrent via peerflix (opens in mpv).

    Args:
        magnet: Magnet URI (magnet:?xt=urn:btih:...)
        title: Optional title for display

    Returns:
        subprocess.Popen instance (you can .wait() or .terminate())

    Raises:
        PeerflixError: If peerflix not installed
    """
    peerflix_cmd = _locate_peerflix()
    cmd = [peerflix_cmd, magnet]

    # peerflix options (passed as separate args)
    if title:
        cmd.extend(["--title", title])
    cmd.extend(["--mpv"])  # Force mpv playback

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PeerflixError(
            f"Could not launch peerflix. Install with: npm install -g peerflix"
        ) from exc

    return proc


def get_status() -> Optional[str]:
    """Check if peerflix is available."""
    try:
        _locate_peerflix()
        return "peerflix ready"
    except PeerflixError as e:
        return None
