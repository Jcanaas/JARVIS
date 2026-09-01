"""Performance helpers: shared image caching, thread pooling, disk cache."""
from __future__ import annotations

import base64
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtGui import QImage, QPixmap


class BoundedCache(dict):
    """An in-memory dict cache that evicts its oldest entry once it grows
    past ``max_size``, instead of holding every key ever inserted for the
    life of the process.

    Meant as a fast-path in front of something slower and durable (a disk
    cache, a network fetch) — losing an old entry just means the next lookup
    falls through to that slower path again, not data loss. Eviction is
    insertion-order FIFO (relies on dict preserving insertion order), not
    true LRU: a re-set of an existing key does not move it to the back. Good
    enough for caches like "avatar bytes by URL" where re-inserts of the
    same key are rare.
    """

    def __init__(self, max_size: int = 500):
        super().__init__()
        self._max_size = max(1, int(max_size))

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        while len(self) > self._max_size:
            del self[next(iter(self))]


class DiskImageCache:
    """LRU disk cache for images (network or local). TTL = 7 days."""

    def __init__(self, subdir: str = "image_cache"):
        cache_root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData/Local"))
        self.dir = cache_root / "Jarvis" / subdir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = 7

    def _key(self, url_or_data: str) -> str:
        h = hashlib.sha256(url_or_data.encode() if isinstance(url_or_data, str) else url_or_data)
        return h.hexdigest()[:16]

    def get(self, url_or_data: str) -> Optional[bytes]:
        """Fetch from disk if fresh, else None."""
        key = self._key(url_or_data)
        path = self.dir / key
        if not path.exists():
            return None
        import time
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days > self.ttl_days:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def put(self, url_or_data: str, data: bytes):
        """Store to disk."""
        key = self._key(url_or_data)
        path = self.dir / key
        try:
            path.write_bytes(data)
        except OSError:
            pass


class SharedThreadPool:
    """Global thread pool for image I/O and decode tasks.

    Worker count follows the "performance_plan" setting (Ajustes →
    Rendimiento) — read once, at creation, since a ThreadPoolExecutor can't
    be resized after the fact; changing the plan takes effect on next
    restart. Falls back to the balanced default (4) if platform_runtime is
    unavailable for any reason.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    try:
                        from actions.platform_runtime import thread_pool_size
                        workers = thread_pool_size()
                    except Exception:
                        workers = 4
                    cls._instance = super().__new__(cls)
                    cls._instance.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jarvis_img")
        return cls._instance

    def submit(self, fn: Callable, *args, **kwargs):
        return self.executor.submit(fn, *args, **kwargs)


class OffThreadImageDecoder:
    """Decode image + scale off-thread, emit signals when done."""

    def __init__(self, on_ready: Callable[[QPixmap], None], on_error: Callable[[], None]):
        self.on_ready = on_ready
        self.on_error = on_error
        self.pool = SharedThreadPool()

    def decode_and_scale(self, raw: bytes, width: int, height: int):
        """Decode and scale off-thread."""
        def work():
            try:
                img = QImage()
                if not img.loadFromData(raw):
                    self.on_error()
                    return
                # Scale with high quality off-thread
                scaled = img.scaledToWidth(
                    width,
                    mode=1  # SmoothTransformation
                )
                if scaled.height() > height:
                    scaled = scaled.scaledToHeight(height, mode=1)
                pix = QPixmap.fromImage(scaled)
                if not pix.isNull():
                    self.on_ready(pix)
                else:
                    self.on_error()
            except Exception:
                self.on_error()

        self.pool.submit(work)
