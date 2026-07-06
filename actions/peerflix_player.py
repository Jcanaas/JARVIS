"""Torrent streaming via local peerflix + mpv.

Launches peerflix (npm package) to stream a torrent magnet link without
downloading the full file. Returns streaming URL for QMediaPlayer playback.

Requires: peerflix installed globally or via npx
  npm install -g peerflix
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from typing import Optional


class PeerflixError(RuntimeError):
    """Raised when peerflix is unavailable or playback fails."""


_peerflix_process: Optional[subprocess.Popen] = None
_peerflix_port = 6881


def _locate_peerflix() -> str:
    """Find peerflix executable: global npm or npx."""
    # Try global install
    peerflix = shutil.which("peerflix")
    if peerflix:
        return peerflix

    # Try npx (comes with npm 5.2+)
    npx = shutil.which("npx")
    if npx:
        return f"{npx} peerflix"

    raise PeerflixError(
        "peerflix not found. Install: npm install -g peerflix"
    )


def _port_available(port: int) -> bool:
    """Check if a port is available."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return result != 0


def play(magnet: str, title: str = "") -> str:
    """Stream a torrent via local peerflix.

    Args:
        magnet: Magnet URI (magnet:?xt=urn:btih:...)
        title: Optional title for display

    Returns:
        HTTP URL to stream (localhost:6881/...)

    Raises:
        PeerflixError: If peerflix not available or fails to start
    """
    global _peerflix_process

    # Kill previous peerflix if running
    stop()

    try:
        peerflix_cmd = _locate_peerflix()
    except PeerflixError:
        raise

    try:
        # Start peerflix in background
        # Format: peerflix <magnet> --port 6881 --mpv
        cmd = peerflix_cmd.split() + [magnet, "--port", str(_peerflix_port), "--mpv"]

        _peerflix_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for peerflix to be ready (port becomes available means it crashed,
        # unavailable means it's listening)
        max_retries = 30
        for attempt in range(max_retries):
            time.sleep(0.5)
            if not _port_available(_peerflix_port):
                # Port is in use = peerflix is listening
                return f"http://127.0.0.1:{_peerflix_port}/"

        # Timeout
        raise PeerflixError("peerflix did not start in time")

    except Exception as exc:
        raise PeerflixError(f"Failed to start peerflix: {exc}") from exc


def stop():
    """Stop the running peerflix process."""
    global _peerflix_process
    if _peerflix_process:
        try:
            _peerflix_process.terminate()
            _peerflix_process.wait(timeout=2)
        except Exception:
            try:
                _peerflix_process.kill()
            except Exception:
                pass
        _peerflix_process = None


def get_status() -> Optional[str]:
    """Check if peerflix is available."""
    try:
        _locate_peerflix()
        return "peerflix ready"
    except PeerflixError:
        return None
