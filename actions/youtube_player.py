"""Embedded YouTube video playback (mpv via --wid) + real YouTube likes.

Two responsibilities:

1. EmbeddedVideoPlayer — drives a dedicated mpv instance whose video is rendered
   inside a Qt widget (passed as a native window id).  Uses its own JSON-IPC named
   pipe so it never collides with the headless music player.

2. YouTube Data API helpers — get_youtube_service()/rate_video() let the user
   like/unlike a video on their real account.  These reuse the shared Google
   token (config/google_token.json); a single OAuth flow grants Calendar, Gmail,
   Drive and YouTube, so the user signs in only once.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from actions.paths import RESOURCE_DIR, config_path

BASE_DIR         = RESOURCE_DIR
CREDENTIALS_FILE = config_path("google_credentials.json")

_PIPE_PATH = r"\\.\pipe\jarvis_mpv_video"


def _locate_mpv() -> str:
    for candidate in (
        BASE_DIR / "mpv.exe",
        BASE_DIR / "tools" / "mpv" / "mpv.exe",
        BASE_DIR / "tools" / "mpv.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return shutil.which("mpv") or shutil.which("mpv.exe") or "mpv"


def _locate_ytdlp() -> Optional[str]:
    for candidate in (
        shutil.which("yt-dlp"),
        shutil.which("yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


_MPV_EXE = _locate_mpv()
_PROCS: list[subprocess.Popen] = []


def _kill_all_procs():
    for proc in list(_PROCS):
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


atexit.register(_kill_all_procs)


class EmbeddedVideoPlayer:
    """Controls a single mpv process embedded into a Qt widget (by window id)."""

    def __init__(self, wid: int, pipe: str = _PIPE_PATH):
        self.wid = int(wid)
        self.pipe = pipe
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ start
    def _mpv_available(self) -> bool:
        try:
            subprocess.run([_MPV_EXE, "--version"], capture_output=True, timeout=3)
            return True
        except Exception:
            return False

    def _wait_for_pipe(self, timeout_ms: int = 6000) -> bool:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline:
                if kernel32.WaitNamedPipeW(self.pipe, 400):
                    return True
                err = kernel32.GetLastError()
                if err in (0, 2, 231):  # not-ready / busy
                    time.sleep(0.08)
                    continue
                return True
            return False
        except Exception:
            time.sleep(1.5)
            return True

    def start(self) -> bool:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return True
            if not self._mpv_available():
                return False
            env = os.environ.copy()
            env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
            args = [
                _MPV_EXE,
                f"--wid={self.wid}",
                f"--input-ipc-server={self.pipe}",
                "--idle=yes",
                "--force-window=yes",
                "--keep-open=yes",
                "--osc=no",
                "--no-border",
                "--input-default-bindings=no",
                "--input-vo-keyboard=no",
                "--ytdl-format=bestvideo[height<=1080]+bestaudio/best/best",
                "--cache=yes",
                "--volume=90",
            ]
            ytdlp_path = _locate_ytdlp()
            if ytdlp_path:
                args.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp_path}")
            try:
                self.proc = subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
                )
            except Exception:
                self.proc = None
                return False
            _PROCS.append(self.proc)
            return self._wait_for_pipe()

    # -------------------------------------------------------------------- ipc
    def _open_pipe(self):
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            self.pipe, 0x40000000 | 0x80000000, 0, None, 3, 0, None
        )
        if handle is None or ctypes.c_void_p(handle).value in (None, -1):
            return None, kernel32
        return handle, kernel32

    def _send(self, cmd: list) -> bool:
        payload = (json.dumps({"command": cmd}) + "\n").encode("utf-8")
        for attempt in range(5):
            try:
                import ctypes
                import ctypes.wintypes as wt
                handle, kernel32 = self._open_pipe()
                if handle is None:
                    time.sleep(0.2)
                    continue
                try:
                    written = wt.DWORD(0)
                    kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None)
                    return True
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                time.sleep(0.2)
        return False

    def _get(self, prop: str):
        payload = (json.dumps({"command": ["get_property", prop], "request_id": 1}) + "\n").encode("utf-8")
        try:
            import ctypes
            import ctypes.wintypes as wt
            handle, kernel32 = self._open_pipe()
            if handle is None:
                return None
            try:
                written = wt.DWORD(0)
                if not kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None):
                    return None
                buf = ctypes.create_string_buffer(65536)
                read = wt.DWORD(0)
                if not kernel32.ReadFile(handle, buf, 65536, ctypes.byref(read), None):
                    return None
                for line in buf.raw[: read.value].decode("utf-8", "ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and obj.get("error") == "success":
                        return obj.get("data")
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
        return None

    # ---------------------------------------------------------------- control
    def play(self, url: str) -> bool:
        if not self.start():
            return False
        return self._send(["loadfile", url, "replace"])

    def toggle(self) -> bool:
        return self._send(["cycle", "pause"])

    def pause(self) -> bool:
        return self._send(["set_property", "pause", True])

    def resume(self) -> bool:
        return self._send(["set_property", "pause", False])

    def stop(self) -> bool:
        return self._send(["stop"])

    def seek_abs(self, seconds: float) -> bool:
        try:
            return self._send(["seek", float(seconds), "absolute"])
        except Exception:
            return False

    def seek_rel(self, seconds: float) -> bool:
        try:
            return self._send(["seek", float(seconds), "relative"])
        except Exception:
            return False

    def set_volume(self, level: int) -> bool:
        return self._send(["set_property", "volume", max(0, min(100, int(level)))])

    def position(self):
        return self._get("time-pos")

    def duration(self):
        return self._get("duration")

    def paused(self):
        return self._get("pause")

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def shutdown(self):
        try:
            if self.proc is not None and self.proc.poll() is None:
                self._send(["quit"])
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    pass
                if self.proc.poll() is None:
                    self.proc.terminate()
        except Exception:
            pass
        self.proc = None


# ----------------------------------------------------------- YouTube Data API
def get_youtube_service():
    """Authorised youtube/v3 client using the shared Google token.

    A single OAuth flow grants Calendar + Gmail + Drive + YouTube, so the user
    authenticates only once for every Google feature in the app.
    """
    from actions.google_auth import get_google_service
    return get_google_service("youtube", "v3")


def _entry_to_video(entry: dict) -> Optional[dict]:
    if not isinstance(entry, dict):
        return None
    vid = entry.get("id") or entry.get("video_id")
    if not vid or len(str(vid)) != 11:
        return None
    return {
        "id": str(vid),
        "title": entry.get("title") or "(sin título)",
        "channel": entry.get("uploader") or entry.get("channel") or entry.get("uploader_id") or "",
        "duration": entry.get("duration") or 0,
    }


def search_videos(query: str, limit: int = 24) -> list[dict]:
    """Fast flat YouTube search (no per-video resolution) for snappy UI results."""
    query = str(query or "").strip()
    if not query:
        return []
    try:
        from yt_dlp import YoutubeDL
    except Exception:
        return []
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }
    out: list[dict] = []
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{int(limit)}:{query}", download=False)
        for entry in (info.get("entries") if isinstance(info, dict) else []) or []:
            video = _entry_to_video(entry)
            if video:
                out.append(video)
    except Exception:
        pass
    return out


def fetch_recommended(limit: int = 24) -> list[dict]:
    """Best-effort 'algorithm' feed: YouTube trending (flat), with a search fallback."""
    try:
        from yt_dlp import YoutubeDL
    except Exception:
        return []
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": int(limit) * 2,
        "socket_timeout": 15,
    }
    out: list[dict] = []
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/trending", download=False)
        entries = (info.get("entries") if isinstance(info, dict) else []) or []
        flat: list = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("entries"):
                flat.extend(entry["entries"])
            else:
                flat.append(entry)
        seen = set()
        for entry in flat:
            video = _entry_to_video(entry)
            if video and video["id"] not in seen:
                seen.add(video["id"])
                out.append(video)
            if len(out) >= limit:
                break
    except Exception:
        out = []
    if not out:
        seen = set()
        for term in ("trending", "noticias", "gaming", "documental"):
            for video in search_videos(term, 8):
                if video["id"] not in seen:
                    seen.add(video["id"])
                    out.append(video)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
    return out


def is_authenticated() -> bool:
    """True if a usable shared Google token exists (no prompt)."""
    from actions.google_auth import is_signed_in
    return is_signed_in()


def fetch_subscriptions_feed(limit: int = 24) -> list[dict]:
    """Personalised feed: most recent uploads from the user's subscriptions."""
    service = get_youtube_service()
    channels: list[tuple[str, str]] = []
    page_token = None
    while len(channels) < 30:
        resp = service.subscriptions().list(
            part="snippet", mine=True, maxResults=50, order="relevance",
            pageToken=page_token,
        ).execute()
        for item in resp.get("items", []):
            snip = item.get("snippet", {})
            cid = (snip.get("resourceId") or {}).get("channelId")
            if cid:
                channels.append((cid, snip.get("title", "")))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    collected: list[dict] = []
    seen = set()
    for cid, cname in channels[:18]:
        uploads_playlist = "UU" + cid[2:]  # uploads playlist id derives from channel id
        try:
            resp = service.playlistItems().list(
                part="snippet", playlistId=uploads_playlist, maxResults=3,
            ).execute()
        except Exception:
            continue
        for item in resp.get("items", []):
            snip = item.get("snippet", {})
            vid = (snip.get("resourceId") or {}).get("videoId")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            collected.append({
                "id": vid,
                "title": snip.get("title", "") or "(sin título)",
                "channel": snip.get("videoOwnerChannelTitle") or cname or snip.get("channelTitle", ""),
                "duration": 0,
                "publishedAt": snip.get("publishedAt", ""),
            })
    collected.sort(key=lambda v: v.get("publishedAt", ""), reverse=True)
    return collected[:limit]


def fetch_video_details(video_id: str) -> dict:
    """Title, channel, description, view count and publish date for a video."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return {}
    service = get_youtube_service()
    resp = service.videos().list(part="snippet,statistics", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        return {}
    snippet = items[0].get("snippet", {}) or {}
    stats = items[0].get("statistics", {}) or {}
    return {
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "description": snippet.get("description", ""),
        "publishedAt": snippet.get("publishedAt", ""),
        "views": stats.get("viewCount", ""),
        "likes": stats.get("likeCount", ""),
    }


def fetch_comments(video_id: str, limit: int = 30) -> list[dict]:
    """Top comments for a video. Returns [] if comments are disabled/unavailable."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return []
    service = get_youtube_service()
    resp = service.commentThreads().list(
        part="snippet", videoId=video_id, order="relevance",
        maxResults=max(1, min(100, int(limit))), textFormat="plainText",
    ).execute()
    out: list[dict] = []
    for item in resp.get("items", []):
        top = (((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet")) or {}
        out.append({
            "author": top.get("authorDisplayName", ""),
            "text": top.get("textDisplay") or top.get("textOriginal") or "",
            "likes": int(top.get("likeCount") or 0),
            "avatar": top.get("authorProfileImageUrl", ""),
        })
    return out


def rate_video(video_id: str, rating: str = "like") -> bool:
    """Rate a video on the user's account. rating: 'like' | 'dislike' | 'none'."""
    video_id = str(video_id or "").strip()
    if not video_id:
        raise ValueError("video_id vacío")
    service = get_youtube_service()
    service.videos().rate(id=video_id, rating=rating).execute()
    return True


def get_rating(video_id: str) -> str:
    """Return the current rating for a video ('like' | 'dislike' | 'none' | '')."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return ""
    try:
        service = get_youtube_service()
        resp = service.videos().getRating(id=video_id).execute()
        items = resp.get("items") or []
        if items:
            return str(items[0].get("rating") or "")
    except Exception:
        return ""
    return ""
