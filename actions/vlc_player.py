"""Torrent streaming for VLC playback via a vendored WebTorrent server.

Launches actions/vendor/webtorrent-stream/stream.mjs (WebTorrent, MIT) with
Node.js, which adds the torrent, picks the largest video file, and serves it
over HTTP. The returned URL is fed to a VLC widget for playback.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from typing import Optional

from actions.paths import find_node, resource

_STREAM_SCRIPT = resource("actions", "vendor", "webtorrent-stream", "stream.mjs")
_READY_TIMEOUT = 50  # seconds to wait for the URL line (metadata + peers)


class VLCPlayerError(RuntimeError):
    """Raised when VLC playback setup fails."""


_stream_process: Optional[subprocess.Popen] = None


def _locate_node() -> str:
    """Find the Node.js executable (bundled runtime first, then PATH)."""
    node = find_node()
    if node:
        return node
    raise VLCPlayerError(
        "Node.js not found. Install it from https://nodejs.org to enable playback."
    )


def start_streaming(magnet: str, title: str = "", file_index: int = -1) -> str:
    """Start a WebTorrent HTTP stream for a magnet link.

    Args:
        magnet: Magnet URI
        title: Optional title (unused, kept for API compatibility)
        file_index: Index of the video file to serve inside the torrent
            (Torrentio's fileIdx). Essential for season/batch torrents where
            the requested episode is not the largest file. -1 = pick largest.

    Returns:
        HTTP URL to the video stream.

    Raises:
        VLCPlayerError: If Node.js is missing or the stream fails to start.
    """
    global _stream_process

    stop_streaming()

    node = _locate_node()

    # No --port: the script binds an ephemeral port and reports the real URL,
    # so repeated plays never clash on a fixed port.
    cmd = [node, str(_STREAM_SCRIPT), magnet]
    if file_index is not None and file_index >= 0:
        cmd += ["--file-index", str(file_index)]
    _stream_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # The script prints one JSON line with the stream URL once the torrent's
    # metadata has arrived. Read it on a worker thread so we can enforce a
    # timeout regardless of how readline blocks.
    result: dict = {}

    def read_url():
        try:
            line = _stream_process.stdout.readline()
            if line:
                result["line"] = line.strip()
        except Exception as exc:  # pragma: no cover - defensive
            result["error"] = str(exc)

    reader = threading.Thread(target=read_url, daemon=True)
    reader.start()
    reader.join(_READY_TIMEOUT)

    if "line" not in result or not result.get("line"):
        # Grab any stderr for a useful message. If the process is still alive it
        # timed out waiting for peers; kill it first so stderr.read() returns.
        err = ""
        proc = _stream_process
        if proc:
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            try:
                err = (proc.stderr.read() or "").strip()
            except Exception:
                pass
        stop_streaming()
        raise VLCPlayerError(
            err or "Stream did not start in time (no seeders or Node error)."
        )

    try:
        data = json.loads(result["line"])
        return data["url"]
    except (json.JSONDecodeError, KeyError) as exc:
        stop_streaming()
        raise VLCPlayerError(f"Invalid stream response: {exc}") from exc


def stop_streaming():
    """Stop the running WebTorrent stream process."""
    global _stream_process
    if _stream_process:
        try:
            _stream_process.terminate()
            _stream_process.wait(timeout=3)
        except Exception:
            try:
                _stream_process.kill()
            except Exception:
                pass
        _stream_process = None


def get_status() -> Optional[str]:
    """Check if Node.js (required for streaming) is available."""
    try:
        _locate_node()
        return "webtorrent ready"
    except VLCPlayerError:
        return None
