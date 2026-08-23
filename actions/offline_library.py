"""Offline playlist library for YouTube Music.

Downloads whole playlists to local audio files, lets playback use those files
while they exist on disk, and re-syncs missing tracks whenever a playlist is
opened.

Files live in the same per-playlist folders the download helpers already use
(``JARVIS_Audio/<title>/NNN - Title [videoId].ext``).  A track is considered
"local" when a file in a registered folder carries its ``[videoId]`` tag, so
lookups never need the network.

A small registry (``offline_playlists.json``) records which playlists the user
marked for offline use and where their folder is.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from actions.paths import config_path

_REGISTRY_FILE = config_path("offline_playlists.json")
_LOCK = threading.Lock()

# The UI exposes one download progress card and one cancel button, so only one
# offline sync may own that channel at a time. This also prevents a manual sync
# and auto-sync from clearing or consuming each other's cancellation signal.
_ACTIVE_SYNCS: dict[str, bool] = {}
_ACTIVE_SYNCS_LOCK = threading.Lock()

# Extensions yt-dlp may leave behind for a "bestaudio" download.
_AUDIO_EXTS = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".oga", ".aac", ".flac", ".wav"}

# videoId -> local path cache, rebuilt lazily with a short TTL and invalidated
# after every sync so freshly downloaded tracks are picked up immediately.
_INDEX_LOCK = threading.Lock()
_index_cache: dict[str, str] = {}
_index_ts: float = 0.0
_INDEX_TTL = 5.0

_VID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _load_registry() -> dict:
    try:
        if _REGISTRY_FILE.exists():
            data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_registry(reg: dict) -> None:
    tmp = _REGISTRY_FILE.with_suffix(".json.tmp")
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(reg, indent=2), encoding="utf-8")
        tmp.replace(_REGISTRY_FILE)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def list_offline() -> dict:
    with _LOCK:
        return _load_registry()


def is_offline(playlist_id: str) -> bool:
    pid = str(playlist_id or "").strip()
    if not pid:
        return False
    with _LOCK:
        return pid in _load_registry()


def is_syncing(playlist_id: str) -> bool:
    pid = str(playlist_id or "").strip()
    if not pid:
        return False
    with _ACTIVE_SYNCS_LOCK:
        return bool(_ACTIVE_SYNCS.get(pid))


def any_syncing() -> bool:
    with _ACTIVE_SYNCS_LOCK:
        return bool(_ACTIVE_SYNCS)


def reserve_sync(playlist_id: str) -> bool:
    """Atomically reserve the app's single offline-download UI channel."""
    pid = str(playlist_id or "").strip()
    if not pid:
        return False
    with _ACTIVE_SYNCS_LOCK:
        if _ACTIVE_SYNCS:
            return False
        _ACTIVE_SYNCS[pid] = True
    return True


def release_sync(playlist_id: str) -> None:
    pid = str(playlist_id or "").strip()
    with _ACTIVE_SYNCS_LOCK:
        _ACTIVE_SYNCS.pop(pid, None)


def mark_offline(playlist_id: str, title: str = "", directory: str = "") -> None:
    pid = str(playlist_id or "").strip()
    if not pid:
        return
    with _LOCK:
        reg = _load_registry()
        entry = reg.get(pid, {})
        if title:
            entry["title"] = title
        if directory:
            entry["dir"] = directory
        entry["updated"] = int(time.time())
        reg[pid] = entry
        _save_registry(reg)


def unmark_offline(playlist_id: str, delete_files: bool = False) -> dict:
    """Stop treating a playlist as offline.  Optionally delete the folder."""
    pid = str(playlist_id or "").strip()
    if not pid:
        return {"removed": False}
    with _ACTIVE_SYNCS_LOCK:
        if _ACTIVE_SYNCS.get(pid):
            return {"removed": False, "busy": True}
    with _LOCK:
        reg = _load_registry()
        entry = reg.pop(pid, None)
        _save_registry(reg)
    invalidate_index()
    if delete_files and entry and entry.get("dir"):
        from actions.ytmusic import _downloads_dir

        managed_root = _downloads_dir("JARVIS_Audio").expanduser().resolve()
        target = Path(entry["dir"]).expanduser().resolve()
        if target == managed_root or not target.is_relative_to(managed_root):
            return {"removed": bool(entry), "unsafe_path": True}
        try:
            import shutil
            shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except Exception as exc:
            return {"removed": bool(entry), "delete_error": str(exc)}
    return {"removed": bool(entry)}


def _playlist_dir(playlist_id: str) -> Optional[Path]:
    with _LOCK:
        entry = _load_registry().get(str(playlist_id or "").strip())
    if entry and entry.get("dir"):
        return Path(entry["dir"])
    return None


# ---------------------------------------------------------------------------
# Local file index
# ---------------------------------------------------------------------------

def _extract_video_id(name: str) -> str:
    matches = _VID_RE.findall(name or "")
    return matches[-1] if matches else ""


def _build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    reg = list_offline()
    for entry in reg.values():
        d = entry.get("dir")
        if not d:
            continue
        index.update(_folder_video_index(Path(d)))
    return index


def _folder_video_index(folder: Path) -> dict[str, str]:
    """Index only one playlist folder, without borrowing another playlist's files."""
    index: dict[str, str] = {}
    if not folder.is_dir():
        return index
    try:
        for file_path in folder.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in _AUDIO_EXTS:
                continue
            vid = _extract_video_id(file_path.name)
            if vid:
                index[vid] = str(file_path)
    except Exception:
        return {}
    return index


def invalidate_index() -> None:
    global _index_ts
    with _INDEX_LOCK:
        _index_ts = 0.0


def _video_index() -> dict[str, str]:
    global _index_cache, _index_ts
    now = time.monotonic()
    with _INDEX_LOCK:
        if _index_cache and (now - _index_ts) < _INDEX_TTL:
            return _index_cache
    index = _build_index()
    with _INDEX_LOCK:
        _index_cache = index
        _index_ts = time.monotonic()
    return index


def _reconcile_order(playlist_dir: Path, tracks: list) -> None:
    """Rename local files so their NNN prefix matches the playlist's current order.

    Adding, removing or reordering tracks in YouTube Music doesn't move files on
    disk by itself, and downloading only the missing tracks would number them
    from 001 within that small batch — colliding with whatever is already there.
    This pass renumbers every file that still has a track in the current list.
    Files whose videoId is no longer in the playlist are left untouched (not
    deleted) since removal is destructive and out of scope here.
    """
    from actions.ytmusic import _safe_filename

    if not playlist_dir.is_dir():
        return

    existing: dict[str, Path] = {}
    try:
        for f in playlist_dir.iterdir():
            if not f.is_file() or f.suffix.lower() not in _AUDIO_EXTS:
                continue
            vid = _extract_video_id(f.name)
            if vid:
                existing[vid] = f
    except Exception:
        return

    targets: dict[str, Path] = {}
    for pos, t in enumerate(tracks, start=1):
        vid = t.get("videoId")
        if not vid:
            continue
        src = existing.get(vid)
        if not src:
            continue
        title = _safe_filename(t.get("title", "track"))
        desired = playlist_dir / f"{pos:03d} - {title} [{vid}]{src.suffix}"
        if desired != src:
            targets[str(src)] = desired

    if not targets:
        return

    # Two-phase rename: positions can swap (e.g. track 3 <-> track 5), so
    # renaming straight to the final name can collide with another file that
    # hasn't moved yet.
    staged: list[tuple[Path, Path, Path]] = []
    for src, dst in targets.items():
        src_path = Path(src)
        tmp_path = src_path.with_name(
            f"._sync_tmp_{time.time_ns()}_{src_path.name}"
        )
        try:
            src_path.rename(tmp_path)
            staged.append((src_path, tmp_path, dst))
        except Exception:
            for original, staged_path, _target in reversed(staged):
                if staged_path.exists():
                    staged_path.rename(original)
            raise

    completed: list[tuple[Path, Path, Path]] = []
    try:
        for original, tmp_path, dst in staged:
            tmp_path.rename(dst)
            completed.append((original, tmp_path, dst))
    except Exception:
        # Restore the entire pre-sync layout. A partial reorder is worse than
        # leaving the old order intact because subsequent scans can see a mix
        # of duplicate positions and hidden staging files.
        for _original, tmp_path, dst in reversed(completed):
            if dst.exists():
                dst.rename(tmp_path)
        for original, tmp_path, _dst in reversed(staged):
            if tmp_path.exists():
                tmp_path.rename(original)
        raise


def local_file_for(video_id: str) -> Optional[str]:
    """Return the on-disk path for a downloaded track, or None."""
    vid = str(video_id or "").strip()
    if not vid:
        return None
    path = _video_index().get(vid)
    if path and Path(path).is_file():
        return path
    return None


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync_playlist(
    playlist_id: str,
    title: str = "",
    progress_hook=None,
    cancel_event=None,
    *,
    _reserved: bool = False,
) -> dict:
    """Download any tracks in the playlist that are not already on disk.

    Always downloads at the highest available quality (``best``), regardless of
    the global download-quality setting.  Registers the playlist as offline.
    """
    from actions.ytmusic import (
        list_playlist_tracks,
        download_audio_tracks,
        _playlist_output_dir,
    )

    pid = str(playlist_id or "").strip()
    if not pid:
        return {"total": 0, "missing": 0, "downloaded": 0, "dir": ""}

    if not _reserved and not reserve_sync(pid):
        # The app exposes one progress card and one cancel button. Running two
        # jobs would merge their progress and make cancellation ambiguous.
        return {"total": 0, "missing": 0, "downloaded": 0, "dir": "", "skipped": "already_syncing"}

    try:
        playlist_dir = _playlist_dir(pid)
        if playlist_dir is None:
            playlist_dir = _playlist_output_dir(pid, output_dir="")
        playlist_dir.mkdir(parents=True, exist_ok=True)

        tracks = list_playlist_tracks(query_or_id=pid, limit=None)
        index = _folder_video_index(playlist_dir)
        missing = [
            t for t in tracks
            if t.get("videoId") and t["videoId"] not in index
        ]

        total = len([t for t in tracks if t.get("videoId")])
        saved: list = []
        if not missing:
            _emit(progress_hook, active=False, percent=100.0,
                  label="Playlist al día", detail=f"{total} canciones en local",
                  can_cancel=False)
        else:
            _emit(progress_hook, active=True, percent=0.0,
                  label="Sincronizando offline",
                  detail=f"{len(missing)} canción(es) nueva(s) de {total}",
                  can_cancel=True)

            saved = download_audio_tracks(
                missing,
                output_dir=str(playlist_dir),
                quality="best",
                progress_hook=progress_hook,
                cancel_event=cancel_event,
            )

        # Renumber existing + newly downloaded files to match the playlist's
        # current order — reordering/adding/removing tracks upstream doesn't
        # move anything on disk by itself.
        _reconcile_order(playlist_dir, tracks)
        # Do not advertise a brand-new playlist as available offline when
        # listing or every attempted download failed. Partial downloads are
        # useful and remain registered so a later sync can complete them.
        if not missing or saved or is_offline(pid):
            mark_offline(pid, title=title, directory=str(playlist_dir))
        invalidate_index()
        return {
            "total": total,
            "missing": len(missing),
            "downloaded": len(saved),
            "dir": str(playlist_dir),
            "complete": not missing or len(saved) == len(missing),
        }
    finally:
        release_sync(pid)


def download_playlist_offline(playlist_id: str, title: str = "", progress_hook=None, cancel_event=None) -> dict:
    """Mark a playlist offline and download every track it is missing."""
    return sync_playlist(playlist_id, title=title, progress_hook=progress_hook, cancel_event=cancel_event)


def _emit(progress_hook, *, active: bool, percent: float, label: str, detail: str, can_cancel: bool = True) -> None:
    if not progress_hook:
        return
    try:
        progress_hook({
            "active": active,
            "percent": percent,
            "label": label,
            "detail": detail,
            "can_cancel": can_cancel,
        })
    except Exception:
        pass
