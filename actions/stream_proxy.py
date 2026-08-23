"""Local HTTP bridge between mpv and YouTube's media CDN.

YouTube stopped serving large byte ranges: a request for ``bytes=0-`` — or for
anything past roughly one mebibyte — comes back ``403 Forbidden``, while a
bounded request under that size is served normally. ffmpeg (and therefore mpv)
always opens a stream with an open-ended range, so every streamed track failed
to load, mpv fell back to re-running yt-dlp, that failed the same way, and the
song simply never started.

This module serves mpv from ``127.0.0.1`` instead: it accepts the open-ended
range mpv insists on, and fulfils it upstream in chunks small enough for the CDN
to answer. Seeking keeps working because the proxy speaks real byte ranges.

Nothing here is reachable from outside the machine: the socket binds to the
loopback interface and each track is addressed by an unguessable token.
"""
from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import requests

# Largest range the CDN answers. Measured: 0-1048576 → 206, 0-1310720 → 403.
CHUNK_BYTES = 1 << 20
_UPSTREAM_TIMEOUT = 20
_MAX_ENTRIES = 8

_lock = threading.Lock()
# Held only while binding the socket, so a burst of registrations (playback
# thread + crossfade thread) cannot each start their own server and leak all
# but one of them.
_start_lock = threading.Lock()
_entries: "OrderedDict[str, dict]" = OrderedDict()
_server: Optional[ThreadingHTTPServer] = None
_port: int = 0


def _content_length_from_url(url: str) -> int:
    """googlevideo puts the exact size in the query as ``clen``."""
    try:
        value = parse_qs(urlparse(url).query).get("clen", [""])[0]
        return int(value)
    except (TypeError, ValueError):
        return 0


def _probe_size(url: str) -> int:
    """Ask for one byte and read the total out of Content-Range."""
    try:
        resp = requests.get(
            url, headers={"Range": "bytes=0-0"}, stream=True, timeout=_UPSTREAM_TIMEOUT
        )
        resp.close()
        rng = resp.headers.get("Content-Range", "")
        if "/" in rng:
            return int(rng.rsplit("/", 1)[1])
    except Exception:
        pass
    return 0


def _parse_range(header: str, total: int) -> tuple[int, int]:
    """Return the inclusive [start, end] the client asked for."""
    start, end = 0, total - 1
    value = str(header or "").strip().lower()
    if value.startswith("bytes="):
        spec = value[6:].split(",", 1)[0].strip()
        first, _, last = spec.partition("-")
        try:
            if first:
                start = max(0, int(first))
                if last:
                    end = min(total - 1, int(last))
            elif last:                      # suffix range: bytes=-N
                start = max(0, total - int(last))
        except ValueError:
            start, end = 0, total - 1
    if end < start:
        end = total - 1
    return start, end


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):                                    # noqa: N802 (stdlib API)
        self._serve(body=False)

    def do_GET(self):                                     # noqa: N802 (stdlib API)
        self._serve(body=True)

    def log_message(self, *_args):
        """Silence the default stderr access log."""

    def _entry(self) -> Optional[dict]:
        token = urlparse(self.path).path.strip("/").split("/")[-1]
        with _lock:
            return _entries.get(token)

    def _serve(self, body: bool) -> None:
        entry = self._entry()
        if entry is None:
            self.send_error(404)
            return
        total = int(entry.get("size") or 0)
        if total <= 0:
            total = _probe_size(entry["url"])
            if total <= 0:
                self.send_error(502)
                return
            with _lock:
                entry["size"] = total

        start, end = _parse_range(self.headers.get("Range", ""), total)
        self.send_response(206 if self.headers.get("Range") else 200)
        self.send_header("Content-Type", entry.get("content_type", "audio/mp4"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if self.headers.get("Range"):
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        if not body:
            return
        try:
            self._pump(entry, start, end)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # mpv moved on (skip, seek, shutdown)

    def _pump(self, entry: dict, start: int, end: int) -> None:
        position = start
        refreshed = False
        while position <= end:
            stop = min(end, position + CHUNK_BYTES - 1)
            try:
                resp = requests.get(
                    entry["url"],
                    headers={"Range": f"bytes={position}-{stop}"},
                    stream=True,
                    timeout=_UPSTREAM_TIMEOUT,
                )
            except Exception:
                return
            if resp.status_code in (403, 404, 410) and not refreshed:
                # The signed URL went stale mid-track. Re-resolve once and pick
                # up exactly where we left off, so playback doesn't drop out.
                resp.close()
                refreshed = True
                fresh = _refresh(entry)
                if not fresh:
                    return
                continue
            if resp.status_code not in (200, 206):
                resp.close()
                return
            try:
                for block in resp.iter_content(64 * 1024):
                    if not block:
                        continue
                    self.wfile.write(block)
                    position += len(block)
            finally:
                resp.close()


def _refresh(entry: dict) -> bool:
    resolver: Optional[Callable[[], tuple[Optional[str], int]]] = entry.get("resolver")
    if resolver is None:
        return False
    try:
        url, _duration = resolver()
    except Exception:
        return False
    if not url:
        return False
    with _lock:
        entry["url"] = url
    return True


def _ensure_server() -> int:
    global _server, _port
    with _lock:
        if _server is not None:
            return _port
    with _start_lock:
        with _lock:
            if _server is not None:
                return _port
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        threading.Thread(
            target=server.serve_forever,
            name="jarvis-stream-proxy",
            daemon=True,
        ).start()
        with _lock:
            _server = server
            _port = server.server_address[1]
            return _port


def serve(
    url: str,
    size: int = 0,
    resolver: Callable[[], tuple[Optional[str], int]] | None = None,
    content_type: str = "audio/mp4",
) -> str:
    """Publish `url` on the loopback proxy and return the address mpv should open.

    `resolver` is called if the upstream URL expires mid-track; it must return
    the same (url, duration) pair the player's own resolution returns.
    """
    url = str(url or "").strip()
    if not url:
        return ""
    port = _ensure_server()
    token = secrets.token_urlsafe(16)
    entry = {
        "url": url,
        "size": int(size or 0) or _content_length_from_url(url),
        "resolver": resolver,
        "content_type": content_type,
    }
    with _lock:
        _entries[token] = entry
        while len(_entries) > _MAX_ENTRIES:
            _entries.popitem(last=False)
    return f"http://127.0.0.1:{port}/stream/{token}"


def shutdown() -> None:
    global _server, _port
    with _lock:
        server, _server, _port = _server, None, 0
        _entries.clear()
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
