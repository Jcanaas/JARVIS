"""Torrent streaming for VLC playback via a persistent WebTorrent daemon.

Talks to actions/vendor/webtorrent-stream/daemon.mjs over a newline-delimited
JSON protocol on stdin/stdout. The daemon is started lazily on first use and
stays alive across plays (one shared WebTorrent client + DHT node + HTTP
server for the whole app session) instead of being spawned fresh and killed
per play — see daemon.mjs's header comment for what that does and doesn't buy
us (mainly a warm DHT routing table + no per-play Node startup cost; tracker
handshakes are NOT cached across plays, verified against bittorrent-tracker's
source).

Public API (start_streaming/stop_streaming/get_status) is unchanged from the
old per-play-subprocess design, so callers don't need to know a daemon exists.
"""
from __future__ import annotations

import json
import subprocess
import threading
import uuid
from typing import Callable, Optional

from actions.paths import find_node, resource

_DAEMON_SCRIPT = resource("actions", "vendor", "webtorrent-stream", "daemon.mjs")
# Metadata for real-world (esp. Spanish tracker) swarms reliably took 45-65s in
# testing, so this needs real margin above that, not a value cutting it close.
_READY_TIMEOUT = 125
_STOP_TIMEOUT = 8
_SHUTDOWN_TIMEOUT = 5


class VLCPlayerError(RuntimeError):
    """Raised when VLC playback setup fails."""


_daemon_proc: Optional[subprocess.Popen] = None
_daemon_lock = threading.RLock()
_reader_thread: Optional[threading.Thread] = None

# State for whichever request is currently in flight. Only one command is ever
# outstanding at a time (serialized by _daemon_lock), so a single slot is
# enough — no need for a full id->waiter registry.
_pending_id: Optional[str] = None
_pending_event = threading.Event()
_pending_result: dict = {}
_pending_progress_hook: Optional[Callable[[int, int], None]] = None


def _locate_node() -> str:
    """Find the Node.js executable (bundled runtime first, then PATH)."""
    node = find_node()
    if node:
        return node
    raise VLCPlayerError(
        "Node.js not found. Install it from https://nodejs.org to enable playback."
    )


def _reader_loop(proc: subprocess.Popen):
    """Background thread: parse the daemon's stdout events for the lifetime of
    the process. Dispatches progress events to the live hook and delivers the
    terminal event (ready/error/stopped/shutdown) for whatever request is
    currently pending."""
    global _pending_result
    try:
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") != _pending_id:
                continue
            if obj.get("type") == "progress":
                hook = _pending_progress_hook
                if hook is not None:
                    try:
                        hook(int(obj.get("peers", 0)), int(obj.get("elapsed", 0)))
                    except Exception:
                        pass
            else:
                _pending_result = obj
                _pending_event.set()
    except Exception:
        pass
    # stdout closed (daemon exited): unblock anyone waiting so they see a
    # clear error instead of hanging until the timeout.
    if not _pending_event.is_set():
        _pending_result = {"type": "error", "message": "El proceso de streaming terminó inesperadamente."}
        _pending_event.set()


def _ensure_daemon() -> subprocess.Popen:
    """Start the daemon if it isn't already running (or restart it if it died)."""
    global _daemon_proc, _reader_thread

    if _daemon_proc is not None and _daemon_proc.poll() is None:
        return _daemon_proc

    node = _locate_node()
    _daemon_proc = subprocess.Popen(
        [node, str(_DAEMON_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,  # line-buffered
    )
    _reader_thread = threading.Thread(
        target=_reader_loop, args=(_daemon_proc,), daemon=True)
    _reader_thread.start()
    return _daemon_proc


def _send_command(cmd: dict, timeout: float) -> dict:
    """Send one command to the daemon and block for its terminal response.

    Must be called with _daemon_lock held.
    """
    global _pending_id, _pending_progress_hook

    # Arm _pending_id/_pending_event BEFORE touching the daemon: if
    # _ensure_daemon() has to (re)spawn the process, its reader thread starts
    # immediately and — should the daemon die on startup — could reach EOF and
    # signal completion before this function got around to arming the wait,
    # silently losing that signal and hanging for the full timeout instead of
    # failing fast.
    req_id = cmd["id"]
    _pending_id = req_id
    _pending_event.clear()

    proc = _ensure_daemon()

    try:
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()
    except Exception as exc:
        raise VLCPlayerError(f"No se pudo hablar con el proceso de streaming: {exc}") from exc

    if not _pending_event.wait(timeout):
        raise VLCPlayerError("El proceso de streaming no respondió a tiempo.")

    return dict(_pending_result)


def start_streaming(magnet: str, title: str = "", file_index: int = -1,
                    progress_hook: Optional[Callable[[int, int], None]] = None) -> str:
    """Start (or switch to) a WebTorrent HTTP stream for a magnet link.

    Args:
        magnet: Magnet URI
        title: Optional title (unused, kept for API compatibility)
        file_index: Index of the video file to serve inside the torrent
            (Torrentio's fileIdx). Essential for season/batch torrents where
            the requested episode is not the largest file. -1 = pick largest.
        progress_hook: Optional callback(peers, elapsed_seconds) invoked every
            few seconds while waiting for metadata. Metadata for real-world
            (esp. Spanish tracker) swarms legitimately takes 45-65s — without
            live feedback that looks identical to a hang.

    Returns:
        HTTP URL to the video stream.

    Raises:
        VLCPlayerError: If Node.js is missing or the stream fails to start.
    """
    global _pending_progress_hook

    with _daemon_lock:
        _pending_progress_hook = progress_hook
        try:
            result = _send_command(
                {"cmd": "play", "id": str(uuid.uuid4()), "magnet": magnet,
                 "fileIndex": file_index if file_index is not None else -1},
                _READY_TIMEOUT,
            )
        finally:
            _pending_progress_hook = None

    rtype = result.get("type")
    if rtype == "ready":
        return result["url"]
    raise VLCPlayerError(result.get("message") or "Stream did not start in time.")


def stop_streaming():
    """Stop the currently playing torrent. Leaves the daemon (and its warm
    DHT routing table) running for the next play."""
    with _daemon_lock:
        if _daemon_proc is None or _daemon_proc.poll() is not None:
            return
        try:
            _send_command({"cmd": "stop", "id": str(uuid.uuid4())}, _STOP_TIMEOUT)
        except VLCPlayerError:
            pass  # best-effort; a stuck daemon gets replaced by _ensure_daemon


def shutdown():
    """Fully stop the daemon process (app exit). Not needed between plays —
    use stop_streaming() for that."""
    global _daemon_proc, _reader_thread
    with _daemon_lock:
        proc = _daemon_proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                _send_command({"cmd": "shutdown", "id": str(uuid.uuid4())}, _SHUTDOWN_TIMEOUT)
            except VLCPlayerError:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _daemon_proc = None
        _reader_thread = None


def get_status() -> Optional[str]:
    """Check if Node.js (required for streaming) is available."""
    try:
        _locate_node()
        return "webtorrent ready"
    except VLCPlayerError:
        return None
