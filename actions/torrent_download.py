"""Torrent-to-disk download via a vendored WebTorrent downloader.

Launches actions/vendor/webtorrent-stream/download.mjs (WebTorrent, MIT) with
Node.js, which adds the torrent and writes it straight to a destination
folder, reporting progress as JSON lines on stdout. Used by the download
button in Movies/Anime (save instead of stream) and by Games (repacks have
no reproduction path, only download).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from actions.paths import find_node, resource

_DOWNLOAD_SCRIPT = resource("actions", "vendor", "webtorrent-stream", "download.mjs")
_READY_TIMEOUT = 50  # seconds to wait for the first progress/error line


class TorrentDownloadError(RuntimeError):
    """Raised when a torrent download fails to start."""


class DownloadHandle:
    """Handle to a running download; use to cancel or check status."""

    def __init__(self, process: subprocess.Popen):
        self._process = process
        self.done = False
        self.error: Optional[str] = None
        self.final_path: Optional[str] = None

    def cancel(self):
        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

    def is_running(self) -> bool:
        return self._process.poll() is None


def _locate_node() -> str:
    node = find_node()
    if node:
        return node
    raise TorrentDownloadError(
        "Node.js not found. Install it from https://nodejs.org to enable downloads."
    )


def default_downloads_dir(subfolder: str = "") -> Path:
    """Windows/user Downloads folder, optionally with a subfolder (e.g. 'Games')."""
    base = Path.home() / "Downloads"
    dest = base / subfolder if subfolder else base
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def start_download(
    magnet: str,
    dest_dir: Optional[Path] = None,
    file_index: int = -1,
    progress_hook: Optional[Callable[[float, float, bool, str], None]] = None,
) -> DownloadHandle:
    """Start a WebTorrent download to disk for a magnet link.

    Args:
        magnet: Magnet URI
        dest_dir: Destination folder (defaults to ~/Downloads)
        file_index: Index of a single file to download inside the torrent
            (-1 = download every file, the right default for game repacks).
        progress_hook: Called with (progress 0-1, download_speed_bytes_s,
            done, final_path_or_empty) on every progress tick and once more
            on completion.

    Returns:
        DownloadHandle to track/cancel the download.

    Raises:
        TorrentDownloadError: If Node.js is missing or the download fails to start.
    """
    node = _locate_node()
    dest = dest_dir or default_downloads_dir()
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [node, str(_DOWNLOAD_SCRIPT), magnet, "--dest", str(dest)]
    if file_index is not None and file_index >= 0:
        cmd += ["--file-index", str(file_index)]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    handle = DownloadHandle(process)

    def pump():
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("done"):
                    handle.done = True
                    handle.final_path = data.get("path", "")
                    if progress_hook:
                        progress_hook(1.0, 0.0, True, handle.final_path or "")
                    return
                if progress_hook:
                    progress_hook(
                        float(data.get("progress") or 0.0),
                        float(data.get("downloadSpeed") or 0.0),
                        False, "",
                    )
        finally:
            if not handle.done:
                # Process ended without a "done" line: either cancelled or errored.
                try:
                    err = (process.stderr.read() or "").strip()
                except Exception:
                    err = ""
                if err and process.poll() not in (0, None):
                    handle.error = err
                    if progress_hook:
                        progress_hook(0.0, 0.0, True, "")

    threading.Thread(target=pump, daemon=True).start()
    return handle
