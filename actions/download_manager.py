"""Persistent, resumable torrent download manager.

Owns every in-progress/finished torrent download in the app (game repacks and
"save instead of stream" movies/anime), tracks them in a JSON state file, and
resumes unfinished ones on the next launch. WebTorrent verifies whatever
already sits in the destination folder when a magnet is re-added, so relaunching
download.mjs with the same magnet + dest continues from where it left off.

UI-agnostic on purpose: the UI registers a listener callback that gets fired
(off the worker thread) whenever any download changes, and marshals it onto the
Qt thread itself. This module never imports Qt.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

from actions.paths import memory_path
from actions import torrent_download as td

_STATE_FILE = memory_path("downloads.json")
_PERSIST_MIN_INTERVAL = 2.0  # seconds between progress-driven disk writes

# status values
DOWNLOADING = "downloading"
PAUSED = "paused"
DONE = "done"
ERROR = "error"


@dataclass
class Download:
    id: str
    title: str
    magnet: str
    dest_dir: str
    file_index: int = -1
    kind: str = "game"           # game | movie | anime
    poster_url: str = ""
    status: str = DOWNLOADING
    progress: float = 0.0        # 0..1
    speed: float = 0.0           # bytes/s (runtime only, meaningless after restart)
    downloaded: int = 0          # bytes
    total: int = 0               # bytes
    final_path: str = ""
    error: str = ""
    # True while the download is meant to be running. Distinguishes a download
    # interrupted by the app closing (auto_resume stays True → resumed on next
    # launch) from one the user deliberately paused (auto_resume False → stays
    # paused). Persisted.
    auto_resume: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class DownloadManager:
    """Use get_manager() rather than constructing directly."""

    def __init__(self):
        self._downloads: dict[str, Download] = {}
        self._handles: dict[str, td.DownloadHandle] = {}
        self._listeners: list[Callable[[list[Download]], None]] = []
        self._lock = threading.RLock()
        self._last_persist = 0.0
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self):
        try:
            if not _STATE_FILE.exists():
                return
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                for rec in json.load(f):
                    fields = Download.__dataclass_fields__
                    d = Download(**{k: rec[k] for k in fields if k in rec})
                    d.speed = 0.0  # live value; never trust a persisted one
                    if d.status == DOWNLOADING:
                        # Was mid-flight when the app closed → park it as paused;
                        # resume_interrupted() restarts it (auto_resume is True).
                        d.status = PAUSED
                    self._downloads[d.id] = d
        except Exception:
            pass

    def _persist(self, force: bool = False):
        now = time.time()
        if not force and now - self._last_persist < _PERSIST_MIN_INTERVAL:
            return
        self._last_persist = now
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump([d.to_dict() for d in self._downloads.values()], f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -- listeners ------------------------------------------------------
    def add_listener(self, cb: Callable[[list[Download]], None]):
        self._listeners.append(cb)
        cb(self.list())  # push current state immediately

    def _notify(self):
        snapshot = self.list()
        for cb in list(self._listeners):
            try:
                cb(snapshot)
            except Exception:
                pass

    def list(self) -> list[Download]:
        with self._lock:
            return sorted(self._downloads.values(),
                          key=lambda d: d.created_at, reverse=True)

    def get(self, download_id: str) -> Optional[Download]:
        with self._lock:
            return self._downloads.get(download_id)

    # -- lifecycle ------------------------------------------------------
    def add_download(self, title: str, magnet: str, dest_dir: Path | str,
                     file_index: int = -1, kind: str = "game",
                     poster_url: str = "") -> str:
        """Register and immediately start a new download. Returns its id.

        De-dupes on infohash: re-requesting a tracked magnet returns the
        existing id (resuming it if it was paused) instead of duplicating.
        """
        infohash = _infohash(magnet)
        with self._lock:
            for d in self._downloads.values():
                if infohash and _infohash(d.magnet) == infohash:
                    if d.status in (PAUSED, ERROR):
                        self.resume(d.id)
                    return d.id
            d = Download(
                id=uuid.uuid4().hex[:12], title=title, magnet=magnet,
                dest_dir=str(dest_dir), file_index=file_index, kind=kind,
                poster_url=poster_url, status=DOWNLOADING, auto_resume=True,
            )
            self._downloads[d.id] = d
        self._persist(force=True)
        self._notify()
        self._start(d.id)
        return d.id

    def _start(self, download_id: str):
        with self._lock:
            d = self._downloads.get(download_id)
            if not d or d.status == DONE:
                return
            d.status = DOWNLOADING
            d.auto_resume = True
            d.error = ""

        def hook(progress: float, speed: float, done: bool, path: str,
                 _id=download_id):
            self._on_progress(_id, progress, speed, done, path)

        try:
            handle = td.start_download(
                d.magnet, dest_dir=Path(d.dest_dir),
                file_index=d.file_index, progress_hook=hook,
            )
            with self._lock:
                self._handles[download_id] = handle
        except Exception as exc:
            with self._lock:
                d.status = ERROR
                d.error = str(exc)
            self._persist(force=True)
            self._notify()

    def _on_progress(self, download_id: str, progress: float, speed: float,
                     done: bool, path: str):
        with self._lock:
            d = self._downloads.get(download_id)
            if not d or d.status != DOWNLOADING:
                return  # a pause/remove raced the pump thread — drop late ticks
            if done:
                if path:
                    d.status = DONE
                    d.progress = 1.0
                    d.speed = 0.0
                    d.auto_resume = False
                    d.final_path = path
                else:
                    handle = self._handles.get(download_id)
                    d.error = (handle.error if handle and handle.error else "") \
                        or "Descarga interrumpida (sin seeders?)"
                    d.status = ERROR
                    d.speed = 0.0
            else:
                d.progress = progress
                d.speed = speed
        self._persist(force=done)
        self._notify()

    def pause(self, download_id: str):
        with self._lock:
            d = self._downloads.get(download_id)
            if not d or d.status != DOWNLOADING:
                return
            d.status = PAUSED
            d.auto_resume = False  # user intent: stay paused across restarts
            d.speed = 0.0
            handle = self._handles.pop(download_id, None)
        if handle:
            handle.cancel()
        self._persist(force=True)
        self._notify()

    def resume(self, download_id: str):
        with self._lock:
            d = self._downloads.get(download_id)
            if not d or d.status in (DOWNLOADING, DONE):
                return
        self._start(download_id)
        self._persist(force=True)
        self._notify()

    def remove(self, download_id: str, delete_files: bool = False):
        with self._lock:
            d = self._downloads.pop(download_id, None)
            handle = self._handles.pop(download_id, None)
        if handle:
            handle.cancel()
        if d and delete_files and d.final_path:
            try:
                p = Path(d.final_path)
                if p.is_dir():
                    import shutil
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink()
            except Exception:
                pass
        self._persist(force=True)
        self._notify()

    def open_folder(self, download_id: str):
        with self._lock:
            d = self._downloads.get(download_id)
            if not d:
                return
            target = Path(d.final_path).parent if d.final_path else Path(d.dest_dir)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # noqa: S606 - user-initiated
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(target)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            pass

    def resume_interrupted(self):
        """Restart downloads interrupted by the app closing (call once on start).

        User-paused downloads (auto_resume False) are left alone; only those
        that were actively downloading last run come back.
        """
        with self._lock:
            ids = [d.id for d in self._downloads.values()
                   if d.status == PAUSED and d.auto_resume]
        for did in ids:
            self.resume(did)


def _infohash(magnet: str) -> str:
    m = re.search(r"btih:([a-zA-Z0-9]+)", magnet or "")
    return m.group(1).lower() if m else ""


_MANAGER: Optional[DownloadManager] = None


def get_manager() -> DownloadManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = DownloadManager()
    return _MANAGER
