"""VLC-based torrent video player integration.

Provides a function to start peerflix streaming and return a configuration
dict for a PyQt6 VLC widget to consume.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from typing import Optional


class VLCPlayerError(RuntimeError):
    """Raised when VLC playback setup fails."""


_peerflix_process: Optional[subprocess.Popen] = None
_peerflix_port = 6881


def _locate_peerflix() -> str:
    """Find peerflix executable: global npm or npx."""
    peerflix = shutil.which("peerflix")
    if peerflix:
        return peerflix
    npx = shutil.which("npx")
    if npx:
        return f"{npx} peerflix"
    raise VLCPlayerError(
        "peerflix not found. Install: npm install -g peerflix"
    )


def _port_available(port: int) -> bool:
    """Check if a port is available."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return result != 0


def start_streaming(magnet: str, title: str = "") -> str:
    """Start peerflix streaming for a magnet link.

    Args:
        magnet: Magnet URI
        title: Optional title for display

    Returns:
        HTTP URL to the stream (localhost:6881/...)

    Raises:
        VLCPlayerError: If peerflix not available or fails to start
    """
    global _peerflix_process

    stop_streaming()

    try:
        peerflix_cmd = _locate_peerflix()
    except VLCPlayerError:
        raise

    try:
        cmd = peerflix_cmd.split() + [magnet, "--port", str(_peerflix_port)]
        _peerflix_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for peerflix to be ready
        max_retries = 30
        for attempt in range(max_retries):
            time.sleep(0.5)
            if not _port_available(_peerflix_port):
                return f"http://127.0.0.1:{_peerflix_port}/"

        raise VLCPlayerError("peerflix did not start in time")

    except Exception as exc:
        raise VLCPlayerError(f"Failed to start peerflix: {exc}") from exc


def stop_streaming():
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
    except VLCPlayerError:
        return None
