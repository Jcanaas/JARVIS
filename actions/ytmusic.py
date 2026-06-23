"""YouTube Music integration for Jarvis.

This module exposes search and metadata helpers plus a dispatcher `ytmusic(...)`
that prefers the headless mpv backend (`actions.ytmusic_headless`) when available.
If headless playback is not available, the dispatcher falls back to opening URLs
in the browser.

For functions that require access to your personal library (liked songs, playlists),
use ytmusicapi OAuth:
    python -c "from ytmusicapi import setup_oauth; setup_oauth('config/ytmusic_oauth.json')"
"""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path
from typing import Dict, List, Optional

try:
    from yt_dlp import YoutubeDL
    _YTDLP_OK = True
except Exception:
    YoutubeDL = None
    _YTDLP_OK = False

from actions.paths import RESOURCE_DIR, config_path

BASE_DIR   = RESOURCE_DIR
OAUTH_FILE = config_path("ytmusic_oauth.json")
GOOGLE_CREDENTIALS_FILE = config_path("google_credentials.json")
DOWNLOAD_STATE_FILE = config_path("download_state.json")
_MUSIC_BASE = "https://music.youtube.com"
_YTMUSIC_AUTH_LOCK = threading.Lock()


class DownloadCancelled(RuntimeError):
    pass


def _load_download_state() -> dict:
    try:
        if DOWNLOAD_STATE_FILE.exists():
            return json.loads(DOWNLOAD_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"audio_quality": "best", "video_quality": "best", "last_failed": []}


def _save_download_state(state: dict) -> None:
    try:
        DOWNLOAD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


_DOWNLOAD_STATE = _load_download_state()
_DOWNLOAD_QUEUE: list[dict] = []
_DOWNLOAD_QUEUE_LOCK = threading.Lock()
_DOWNLOAD_QUEUE_RUNNING = False
_DOWNLOAD_PAUSED = threading.Event()
_DOWNLOAD_CANCEL_ALL = threading.Event()


def _fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _artist_names(artists) -> str:
    if not artists:
        return ""
    if isinstance(artists, dict):
        return str(artists.get("name") or artists.get("title") or artists.get("text") or "")
    if isinstance(artists, list):
        names = []
        for a in artists:
            if isinstance(a, dict):
                names.append(str(a.get("name") or a.get("title") or a.get("text") or ""))
            else:
                names.append(str(a))
        return ", ".join(n for n in names if n)
    return str(artists)


def _upgrade_thumbnail_url(url: str, size: int = 1200) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if "googleusercontent.com" not in url and "ggpht.com" not in url:
        return url

    base, sep, params = url.rpartition("=")
    if not sep or "/" not in base:
        return f"{url}=w{size}-h{size}-l90-rj"
    if re.match(r"^(w\d+|h\d+|s\d+|p\d+|cc)[A-Za-z0-9_-]*", params):
        rounded = "-rj" if "-rj" in params else ""
        return f"{base}=w{size}-h{size}-l90{rounded}"
    return url


def _cover_match_score(expected: str, candidate: str) -> int:
    e = _normalize_text(expected)
    c = _normalize_text(candidate)
    if not e or not c:
        return 0
    if e == c:
        return 4
    if e in c or c in e:
        return 3
    e2 = re.sub(r"\b(remaster(ed)?|deluxe|edition|explicit|clean|mono|stereo|single|version)\b", "", e)
    c2 = re.sub(r"\b(remaster(ed)?|deluxe|edition|explicit|clean|mono|stereo|single|version)\b", "", c)
    e2 = re.sub(r"\s+", " ", e2).strip()
    c2 = re.sub(r"\s+", " ", c2).strip()
    if e2 and c2 and (e2 == c2 or e2 in c2 or c2 in e2):
        return 2
    return 0


def _itunes_cover_url(title: str = "", artists: str = "", album: str = "") -> str:
    title = str(title or "").strip()
    artists = str(artists or "").strip()
    album = str(album or "").strip()
    if not title and not album:
        return ""
    term = " ".join(part for part in (title, artists, album) if part)
    params = urlencode({
        "term": term,
        "media": "music",
        "entity": "song",
        "limit": 12,
    })
    url = f"https://itunes.apple.com/search?{params}"
    try:
        req = Request(url, headers={"User-Agent": "JARVIS/1.0"})
        with urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return ""

    best_url = ""
    best_score = 0
    artist_parts = [part.strip() for part in re.split(r",|&| and | feat\.? | ft\.? ", artists, flags=re.I) if part.strip()]
    for item in payload.get("results") or []:
        art = str(item.get("artworkUrl100") or item.get("artworkUrl60") or "")
        if not art:
            continue
        score = _cover_match_score(title, item.get("trackName", ""))
        if artist_parts:
            score += max((_cover_match_score(part, item.get("artistName", "")) for part in artist_parts), default=0)
        else:
            score += _cover_match_score(artists, item.get("artistName", ""))
        if album:
            score += _cover_match_score(album, item.get("collectionName", ""))
        if score > best_score:
            best_score = score
            best_url = art

    if best_score < 6 or not best_url:
        return ""
    return re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", best_url)


def _thumbnail_url(data, target_size: int = 0) -> str:
    if not data:
        return ""
    url = ""
    if isinstance(data, dict):
        thumbs = data.get("thumbnails") or data.get("thumbnail") or data.get("artistThumbnails")
        if isinstance(thumbs, list):
            candidates = [item for item in thumbs if isinstance(item, dict) and item.get("url")]
            if candidates:
                best = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
                url = str(best.get("url"))
        if isinstance(thumbs, dict) and isinstance(thumbs.get("thumbnails"), list):
            url = _thumbnail_url(thumbs)
        if isinstance(thumbs, dict) and thumbs.get("url"):
            url = str(thumbs.get("url"))
    if target_size and url:
        return _upgrade_thumbnail_url(url, target_size)
    return url


def _track_result(t: dict) -> Dict:
    album = t.get("album", {})
    artists_raw = t.get("artists")
    artist_ids = [
        str(a.get("id") or a.get("browseId") or "")
        for a in artists_raw or []
        if isinstance(a, dict) and (a.get("id") or a.get("browseId"))
    ] if isinstance(artists_raw, list) else []
    return {
        "title": t.get("title", ""),
        "artists": _artist_names(t.get("artists")),
        "album": album.get("name", "") if isinstance(album, dict) else "",
        "albumId": album.get("id", "") if isinstance(album, dict) else "",
        "duration": t.get("duration") or _fmt_duration(t.get("duration_seconds")),
        "dateAdded": t.get("dateAdded") or t.get("date_added") or t.get("feedbackTimestamp") or "",
        "videoId": t.get("videoId", ""),
        "thumbnail": _thumbnail_url(t),
        "videoType": t.get("videoType", ""),
        "artistIds": artist_ids,
        "artistId": artist_ids[0] if artist_ids else "",
        "inLibrary": t.get("inLibrary"),
        "likeStatus": t.get("likeStatus", ""),
    }


def _liked_music_tracks(tracks, limit: int) -> List[Dict]:
    """Prefer actual YT Music audio tracks over raw liked YouTube videos."""
    clean = []
    fallback = []
    for t in tracks or []:
        if not isinstance(t, dict) or not t.get("videoId"):
            continue
        if t.get("isAvailable") is False:
            continue
        video_type = str(t.get("videoType") or "")
        item = _track_result(t)
        if video_type == "MUSIC_VIDEO_TYPE_ATV":
            clean.append(item)
        elif video_type in ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_PRIVATELY_OWNED_TRACK"):
            fallback.append(item)

    out = clean[:limit]
    if len(out) < limit:
        out.extend(fallback[: limit - len(out)])
    return out[:limit]


def _song_url(videoId: str) -> str:
    return f"https://music.youtube.com/watch?v={videoId}"


def _artist_url(browseId: str) -> str:
    return f"https://music.youtube.com/artist/{browseId}"


def _album_url(browseId: str) -> str:
    return f"https://music.youtube.com/playlist?list={browseId}"


def _downloads_dir(subdir: str = "JARVIS_YTMusic") -> Path:
    out = Path.home() / "Downloads" / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_filename(text: str, fallback: str = "track") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned[:120] or fallback


def _normalize_text(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "si", "sí")


def _optional_limit(limit) -> int | None:
    if limit is None:
        return None
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_liked_playlist_query(query_or_id: str) -> bool:
    q = str(query_or_id or "").strip()
    qn = _normalize_text(q)
    return q.upper() == "LM" or qn in {
        "liked",
        "liked songs",
        "liked music",
        "canciones que te gustan",
        "canciones que me gustan",
        "mis me gusta",
        "me gusta",
        "likes",
    }


def _track_url(track) -> str:
    if isinstance(track, dict):
        vid = track.get("videoId") or track.get("video_id")
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        url = track.get("url") or track.get("webpage_url")
        if url:
            return str(url)
    elif isinstance(track, str):
        t = track.strip()
        if t.startswith("http://") or t.startswith("https://"):
            return t
        if len(t) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", t):
            return f"https://www.youtube.com/watch?v={t}"
    return ""


def _resolve_playlist_id(yt, query_or_id: str) -> str:
    q = (query_or_id or "").strip()
    if q and q.upper() in {"LM", "RDPN", "SE"}:
        return q.upper()
    if q and q.startswith(("VL", "PL", "OLAK5")):
        return q

    if q:
        try:
            pls = yt.get_library_playlists(limit=None)
            ql = q.lower()
            for p in pls:
                pid = str(p.get("playlistId") or p.get("browseId") or "")
                pid_l = pid.lower()
                if ql in (pid_l, f"vl{pid_l}"):
                    return pid
                ttl = str(p.get("title", "")).lower()
                if ql in ttl:
                    return pid
        except Exception:
            pass

        try:
            sr = yt.search(q, filter="playlists", limit=1)
            if sr:
                return sr[0].get("playlistId") or sr[0].get("browseId") or ""
        except Exception:
            pass
        return ""

    try:
        pls = yt.get_library_playlists(limit=None)
        if pls:
            return pls[0].get("playlistId") or pls[0].get("browseId") or ""
    except Exception:
        pass
    return ""


def _resolve_playlist_title(yt, playlist_id: str, fallback: str = "playlist") -> str:
    try:
        pl = yt.get_playlist(playlist_id, limit=1)
        title = str(pl.get("title") or pl.get("name") or "").strip()
        return title or fallback
    except Exception:
        return fallback


def _playlist_output_dir(query_or_id: str, output_dir: str = "") -> Path:
    base = Path(output_dir).expanduser() if output_dir else _downloads_dir("JARVIS_Audio")
    yt = _get_ytmusic(require_auth=True)
    pid = _resolve_playlist_id(yt, query_or_id)
    title = _resolve_playlist_title(yt, pid, fallback="playlist") if pid else "playlist"
    out = base / _safe_filename(title, "playlist")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _normalize_quality(quality: str) -> str:
    q = _normalize_text(quality)
    if q in ("best", "max", "highest", "mejor", "alta", "high", ""):
        return "best"
    if q in ("baja", "low", "96", "96k", "sd"):
        return "low"
    if q in ("media", "medium", "160", "160k", "hd"):
        return "medium"
    if q in ("alta", "high", "320", "320k", "best", "hq"):
        return "high"
    return "best"


def set_default_quality(audio: str = "", video: str = "") -> dict:
    if audio:
        _DOWNLOAD_STATE["audio_quality"] = _normalize_quality(audio)
    if video:
        _DOWNLOAD_STATE["video_quality"] = _normalize_quality(video)
    _save_download_state(_DOWNLOAD_STATE)
    return {
        "audio_quality": _DOWNLOAD_STATE.get("audio_quality", "best"),
        "video_quality": _DOWNLOAD_STATE.get("video_quality", "best"),
    }


def download_status() -> dict:
    with _DOWNLOAD_QUEUE_LOCK:
        queued = list(_DOWNLOAD_QUEUE)
    return {
        "audio_quality": _DOWNLOAD_STATE.get("audio_quality", "best"),
        "video_quality": _DOWNLOAD_STATE.get("video_quality", "best"),
        "last_failed": _DOWNLOAD_STATE.get("last_failed", []),
        "queued_downloads": queued,
    }


def download_status_verbose() -> dict:
    status = download_status()
    audio_dir = _downloads_dir("JARVIS_Audio")
    video_dir = _downloads_dir("JARVIS_Videos")
    return {
        **status,
        "paused": _DOWNLOAD_PAUSED.is_set(),
        "cancel_requested": _DOWNLOAD_CANCEL_ALL.is_set(),
        "queue_running": _DOWNLOAD_QUEUE_RUNNING,
        "failed_count": len(status.get("last_failed", [])),
        "queued_count": len(status.get("queued_downloads", [])),
        "audio_folder": str(audio_dir),
        "video_folder": str(video_dir),
    }


def download_pause(paused: bool = True) -> dict:
    if _as_bool(paused):
        _DOWNLOAD_PAUSED.set()
    else:
        _DOWNLOAD_PAUSED.clear()
    return {"paused": _DOWNLOAD_PAUSED.is_set()}


def download_cancel_all() -> dict:
    _DOWNLOAD_CANCEL_ALL.set()
    _DOWNLOAD_PAUSED.clear()
    with _DOWNLOAD_QUEUE_LOCK:
        cancelled_queued = len(_DOWNLOAD_QUEUE)
        _DOWNLOAD_QUEUE.clear()
    return {"cancel_requested": True, "cleared_queue_items": cancelled_queued}


def playlist_preview(query_or_id: str = "", limit: int = 5) -> dict:
    yt = _get_ytmusic(require_auth=True)
    pid = _resolve_playlist_id(yt, query_or_id)
    if not pid:
        return {}
    preview_limit = max(1, min(25, int(limit or 5)))
    pl = yt.get_playlist(pid, limit=preview_limit)
    tracks = []
    for t in pl.get("tracks") or []:
        tracks.append(_track_result(t))
    return {
        "playlist_id": pid,
        "title": pl.get("title") or pl.get("name") or query_or_id,
        "track_count": pl.get("trackCount") or pl.get("count") or len(tracks),
        "preview_count": len(tracks),
        "tracks": tracks,
        "thumbnail": _thumbnail_url(pl),
    }


def list_playlists(limit: int | None = None) -> List[Dict]:
    yt = _get_ytmusic(require_auth=True)
    pls = yt.get_library_playlists(limit=_optional_limit(limit))
    out = []
    seen: set[str] = set()

    liked_thumb = ""
    try:
        liked_preview = get_liked_songs(limit=1)
        if isinstance(liked_preview, list) and liked_preview:
            liked_thumb = _thumbnail_url(liked_preview[0])
    except Exception:
        liked_thumb = ""

    out.append({
        "playlistId": "LM",
        "browseId": "LM",
        "title": "Canciones que te gustan",
        "author": "YouTube Music",
        "itemCount": "",
        "thumbnail": liked_thumb,
        "url": _album_url("LM"),
    })
    seen.add("lm")

    for p in pls or []:
        pid = p.get("playlistId") or p.get("browseId") or ""
        key = str(pid or p.get("title") or "").strip().lower()
        if key in seen or _is_liked_playlist_query(pid) or _is_liked_playlist_query(p.get("title", "")):
            continue
        seen.add(key)
        out.append({
            "playlistId": pid,
            "title": p.get("title", ""),
            "author": _artist_names(p.get("author")),
            "itemCount": p.get("count", p.get("trackCount", "")),
            "thumbnail": _thumbnail_url(p),
            "url": _album_url(pid) if pid else "",
        })
    return out


def search_playlists(query: str, limit: int = 20) -> List[Dict]:
    q = str(query or "").strip()
    if not q:
        return []
    lim = max(1, min(50, int(limit or 20)))
    out: list[dict] = []
    seen: set[str] = set()

    def add_playlist(p: dict):
        pid = p.get("playlistId") or p.get("browseId") or p.get("id") or ""
        key = pid or str(p.get("title") or p.get("name") or "").lower()
        if not key or key in seen:
            return
        seen.add(key)
        count = p.get("count", p.get("trackCount", p.get("itemCount", "")))
        out.append({
            "playlistId": pid,
            "browseId": p.get("browseId", ""),
            "title": p.get("title") or p.get("name") or "",
            "author": _artist_names(p.get("author") or p.get("artists") or p.get("artist")),
            "itemCount": count,
            "thumbnail": _thumbnail_url(p),
            "url": _album_url(pid) if pid else "",
        })

    if _is_liked_playlist_query(q):
        add_playlist({
            "playlistId": "LM",
            "browseId": "LM",
            "title": "Canciones que te gustan",
            "author": "YouTube Music",
        })

    try:
        yt_auth = _get_ytmusic(require_auth=True)
        qn = _normalize_text(q)
        for p in yt_auth.get_library_playlists(limit=None) or []:
            if qn in _normalize_text(p.get("title", "")):
                add_playlist(p)
                if len(out) >= lim:
                    return out[:lim]
    except Exception:
        pass

    yt = _get_ytmusic()
    for p in yt.search(q, filter="playlists", limit=lim) or []:
        add_playlist(p)
        if len(out) >= lim:
            break
    return out[:lim]


def open_download_folder(kind: str = "audio") -> str:
    sub = "JARVIS_Audio" if str(kind).lower().startswith("a") else "JARVIS_Videos"
    folder = _downloads_dir(sub)
    try:
        subprocess.Popen(["explorer", str(folder)])
    except Exception:
        pass
    return str(folder)


def cleanup_partial_downloads(kind: str = "audio") -> list[str]:
    base = _downloads_dir("JARVIS_Audio" if str(kind).lower().startswith("a") else "JARVIS_Videos")
    removed: list[str] = []
    for path in base.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".part", ".ytdl", ".tmp"):
            try:
                path.unlink()
                removed.append(str(path))
            except Exception:
                pass
    return removed


def retry_failed_downloads(output_dir: str = "", progress_hook=None, cancel_event=None) -> list[str]:
    _DOWNLOAD_CANCEL_ALL.clear()
    failed = list(_DOWNLOAD_STATE.get("last_failed", []))
    if not failed:
        return []
    retried = []
    for item in failed:
        try:
            url = item.get("url") or ""
            quality = item.get("quality") or _DOWNLOAD_STATE.get("audio_quality", "best")
            if url:
                retried.extend(download_audio_tracks(
                    [{"title": item.get("title", "track"), "url": url}],
                    output_dir=output_dir,
                    quality=quality,
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                ))
        except Exception:
            continue
    _DOWNLOAD_STATE["last_failed"] = []
    _save_download_state(_DOWNLOAD_STATE)
    return retried


def download_resume_failed(output_dir: str = "", progress_hook=None, cancel_event=None) -> list[str]:
    return retry_failed_downloads(output_dir=output_dir, progress_hook=progress_hook, cancel_event=cancel_event)


def _download_cancel_requested(cancel_event=None) -> bool:
    return _DOWNLOAD_CANCEL_ALL.is_set() or (cancel_event is not None and cancel_event.is_set())


def _wait_if_paused(cancel_event=None):
    while _DOWNLOAD_PAUSED.is_set() and not _download_cancel_requested(cancel_event):
        time.sleep(0.25)


def download_selected_range(
    query_or_id: str = "",
    start: int = 1,
    end: int = 25,
    output_dir: str = "",
    quality: str = "",
    progress_hook=None,
    cancel_event=None,
) -> List[str]:
    _DOWNLOAD_CANCEL_ALL.clear()
    start_i = max(1, int(start or 1))
    end_i = max(start_i, int(end or start_i))
    tracks = list_playlist_tracks(query_or_id=query_or_id, limit=end_i, shuffle=False)
    selected = tracks[start_i - 1:end_i]
    playlist_dir = _playlist_output_dir(query_or_id, output_dir=output_dir)
    return download_audio_tracks(
        selected,
        output_dir=str(playlist_dir),
        quality=quality or _DOWNLOAD_STATE.get("audio_quality", "best"),
        progress_hook=progress_hook,
        cancel_event=cancel_event,
    )


def download_playlist_range(
    query_or_id: str = "",
    start: int = 1,
    end: int = 25,
    output_dir: str = "",
    quality: str = "",
    progress_hook=None,
    cancel_event=None,
) -> List[str]:
    return download_selected_range(
        query_or_id=query_or_id,
        start=start,
        end=end,
        output_dir=output_dir,
        quality=quality,
        progress_hook=progress_hook,
        cancel_event=cancel_event,
    )


def _queue_worker(progress_hook=None, cancel_event=None):
    global _DOWNLOAD_QUEUE_RUNNING
    try:
        while True:
            with _DOWNLOAD_QUEUE_LOCK:
                if not _DOWNLOAD_QUEUE:
                    _DOWNLOAD_QUEUE_RUNNING = False
                    return
                job = _DOWNLOAD_QUEUE.pop(0)
            try:
                download_playlist_audio(
                    query_or_id=job.get("query", ""),
                    limit=int(job.get("limit", 1000) or 1000),
                    output_dir=job.get("output_dir", ""),
                    shuffle=_as_bool(job.get("shuffle", False)),
                    quality=job.get("quality") or _DOWNLOAD_STATE.get("audio_quality", "best"),
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                )
            except Exception:
                continue
    finally:
        with _DOWNLOAD_QUEUE_LOCK:
            _DOWNLOAD_QUEUE_RUNNING = False


def queue_playlist_download(
    query_or_id: str = "",
    limit: int = 1000,
    output_dir: str = "",
    shuffle: bool = False,
    quality: str = "",
    progress_hook=None,
    cancel_event=None,
) -> dict:
    global _DOWNLOAD_QUEUE_RUNNING
    _DOWNLOAD_CANCEL_ALL.clear()
    job = {
        "query": query_or_id,
        "limit": int(limit or 1000),
        "output_dir": output_dir,
        "shuffle": bool(shuffle),
        "quality": quality or _DOWNLOAD_STATE.get("audio_quality", "best"),
    }
    with _DOWNLOAD_QUEUE_LOCK:
        _DOWNLOAD_QUEUE.append(job)
        queue_len = len(_DOWNLOAD_QUEUE)
        should_start = not _DOWNLOAD_QUEUE_RUNNING
        if should_start:
            _DOWNLOAD_QUEUE_RUNNING = True
    if should_start:
        threading.Thread(target=_queue_worker, kwargs={"progress_hook": progress_hook, "cancel_event": cancel_event}, daemon=True).start()
    return {"queued": True, "queue_length": queue_len, "job": job}


def _audio_format_selector(quality: str) -> str:
    q = _normalize_quality(quality)
    if q == "low":
        return "bestaudio[abr<=96]/bestaudio"
    if q == "medium":
        return "bestaudio[abr<=160]/bestaudio"
    if q == "high":
        return "bestaudio[abr<=320]/bestaudio"
    return "bestaudio/best"


def _emit_download_state(progress_hook, *, active: bool, percent: float, label: str, detail: str, can_cancel: bool = True):
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


def list_playlist_tracks(query_or_id: str = "", limit: int | None = None, shuffle: bool = False) -> List[Dict]:
    """Return every track from a playlist as a list of dicts."""
    yt = _get_ytmusic(require_auth=True)
    if _is_liked_playlist_query(query_or_id):
        out = get_liked_songs(limit=limit)
        if shuffle and out:
            import random
            random.shuffle(out)
        return out

    pid = _resolve_playlist_id(yt, query_or_id)
    if not pid:
        return []

    resolved_limit = _optional_limit(limit)
    pl = yt.get_playlist(pid, limit=resolved_limit)
    tracks = pl.get("tracks") or []
    out = []
    for t in tracks:
        vid = t.get("videoId")
        if not vid:
            continue
        item = _track_result(t)
        item["url"] = _song_url(vid)
        out.append(item)
    if shuffle and out:
        import random
        random.shuffle(out)
    return out if resolved_limit is None else out[:resolved_limit]


def playlist_track_names(tracks) -> List[str]:
    """Extract track names from a list of track dicts or raw strings."""
    names: List[str] = []
    for t in tracks or []:
        if isinstance(t, dict):
            title = str(t.get("title", "")).strip()
            artists = _artist_names(t.get("artists"))
            if title and artists:
                names.append(f"{title} — {artists}")
            elif title:
                names.append(title)
        elif t:
            txt = str(t).strip()
            if txt:
                names.append(txt)
    return names


def download_audio_tracks(tracks, output_dir: str = "", quality: str = "", progress_hook=None, cancel_event=None) -> List[str]:
    """Download each track in a list and return the saved file paths."""
    if not _YTDLP_OK:
        raise RuntimeError("yt-dlp no está disponible en este entorno.")

    items = tracks or []
    outdir = Path(output_dir).expanduser() if output_dir else _downloads_dir("JARVIS_Audio")
    outdir.mkdir(parents=True, exist_ok=True)
    format_selector = _audio_format_selector(quality)

    saved: List[str] = []
    total = max(1, len(items))
    for idx, track in enumerate(items, start=1):
        _wait_if_paused(cancel_event)
        if _download_cancel_requested(cancel_event):
            _emit_download_state(progress_hook, active=False, percent=((idx - 1) / total) * 100.0, label="Download cancelled", detail=f"{idx - 1}/{total} file(s)", can_cancel=False)
            return saved

        url = _track_url(track)
        if not url:
            continue

        title = _safe_filename(
            track.get("title", f"track_{idx}") if isinstance(track, dict) else f"track_{idx}"
        )
        outtmpl = str(outdir / f"{idx:03d} - {title} [%(id)s].%(ext)s")

        _emit_download_state(
            progress_hook,
            active=True,
            percent=((idx - 1) / total) * 100.0,
            label="Preparing audio download",
            detail=f"{idx}/{total} · {title}",
            can_cancel=True,
        )

        def _hook(data: dict):
            _wait_if_paused(cancel_event)
            if _download_cancel_requested(cancel_event):
                raise DownloadCancelled()
            status = data.get("status")
            if status == "downloading":
                downloaded = float(data.get("downloaded_bytes") or 0)
                total_bytes = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                inner = (downloaded / total_bytes * 100.0) if total_bytes > 0 else 0.0
                overall = (((idx - 1) + (inner / 100.0)) / total) * 100.0
                detail = f"{idx}/{total} · {title}"
                speed = data.get("speed")
                eta = data.get("eta")
                extra = []
                if speed:
                    try:
                        extra.append(f"{float(speed) / 1024 / 1024:.1f} MB/s")
                    except Exception:
                        pass
                if eta is not None:
                    extra.append(f"ETA {int(eta)}s")
                if extra:
                    detail = f"{detail} · " + " · ".join(extra)
                _emit_download_state(progress_hook, active=True, percent=overall, label="Downloading audio", detail=detail, can_cancel=True)
            elif status == "finished":
                _emit_download_state(progress_hook, active=True, percent=(idx / total) * 100.0, label="Finalizing audio", detail=f"{idx}/{total} · {title}", can_cancel=True)

        opts = {
            "format": format_selector,
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": False,
            "progress_hooks": [_hook],
        }

        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    saved.append(str(Path(ydl.prepare_filename(info))))
        except DownloadCancelled:
            _emit_download_state(progress_hook, active=False, percent=((idx - 1) / total) * 100.0, label="Download cancelled", detail=f"{idx}/{total} · {title}", can_cancel=False)
            return saved
        except Exception as e:
            _DOWNLOAD_STATE.setdefault("last_failed", []).append({
                "title": title,
                "url": url,
                "quality": quality or _DOWNLOAD_STATE.get("audio_quality", "best"),
                "error": str(e)[:120],
            })
            _save_download_state(_DOWNLOAD_STATE)
            _emit_download_state(
                progress_hook,
                active=True,
                percent=((idx - 1) / total) * 100.0,
                label="Skipping unavailable track",
                detail=f"{idx}/{total} · {title} · {str(e)[:80]}",
                can_cancel=True,
            )
            continue

    _emit_download_state(progress_hook, active=False, percent=100.0 if saved else 0.0, label="Download complete", detail=f"{len(saved)} file(s)", can_cancel=False)

    return saved


def download_playlist_audio(query_or_id: str = "", limit: int = 1000, output_dir: str = "", shuffle: bool = False, quality: str = "", progress_hook=None, cancel_event=None) -> List[str]:
    """List a playlist, then download the audio for every track in it."""
    _DOWNLOAD_CANCEL_ALL.clear()
    tracks = list_playlist_tracks(query_or_id=query_or_id, limit=limit, shuffle=shuffle)
    playlist_dir = _playlist_output_dir(query_or_id, output_dir=output_dir)
    return download_audio_tracks(tracks, output_dir=str(playlist_dir), quality=quality, progress_hook=progress_hook, cancel_event=cancel_event)


def download_liked_audio(limit: int = 25, output_dir: str = "", shuffle: bool = False, quality: str = "", progress_hook=None, cancel_event=None) -> List[str]:
    """Download the user's liked songs as audio files."""
    _DOWNLOAD_CANCEL_ALL.clear()
    songs = get_liked_songs(limit=max(1, min(500, int(limit))))
    if shuffle and songs:
        import random
        random.shuffle(songs)
    return download_audio_tracks(songs, output_dir=output_dir, quality=quality, progress_hook=progress_hook, cancel_event=cancel_event)


# ---------------------------------------------------------------------------
# ytmusicapi client
# ---------------------------------------------------------------------------

def _get_ytmusic(require_auth: bool = False):
    """Return a YTMusic client. Uses OAuth if available, else unauthenticated."""
    from ytmusicapi import YTMusic
    if OAUTH_FILE.exists():
        try:
            return YTMusic(str(OAUTH_FILE))
        except Exception:
            pass
    if require_auth:
        return _refresh_ytmusic_auth()
    return YTMusic()


def _save_ytmusic_headers(headers: Dict[str, str]) -> None:
    """Save captured browser headers to OAUTH_FILE in a format ytmusicapi accepts."""
    try:
        from ytmusicapi import setup as yt_setup
        headers_raw = "\n".join(f"{k}: {v}" for k, v in headers.items())
        yt_setup(filepath=str(OAUTH_FILE), headers_raw=headers_raw)
        return
    except Exception:
        pass
    # Fallback: write the headers dict directly as JSON (works with most versions)
    OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    OAUTH_FILE.write_text(json.dumps(headers, indent=2), encoding="utf-8")


def _capture_ytmusic_browser_headers(timeout_seconds: int = 300) -> Dict[str, str]:
    """Open YouTube Music in Chromium and capture an authenticated API request."""
    from playwright.sync_api import sync_playwright
    from actions.browser_control import _resolve_browser

    profile = Path.home() / ".jarvis_profiles" / "ytmusic_auth"
    profile.mkdir(parents=True, exist_ok=True)
    captured: Dict[str, str] = {}

    with sync_playwright() as playwright:
        browser_type = playwright.chromium
        launch_args: Dict = {
            "headless": False,
            "no_viewport": True,
            "args": [
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
        }

        spec = _resolve_browser("edge") or _resolve_browser("chrome")
        if spec:
            if spec.get("exe"):
                launch_args["executable_path"] = spec["exe"]
            elif spec.get("channel"):
                launch_args["channel"] = spec["channel"]

        context = browser_type.launch_persistent_context(str(profile), **launch_args)
        page = context.pages[0] if context.pages else context.new_page()

        def _inspect_request(request) -> None:
            if captured or "youtubei/v1/" not in request.url:
                return
            try:
                hdrs = {str(k).lower(): str(v) for k, v in request.all_headers().items()}
            except Exception:
                return
            if (
                hdrs.get("cookie")
                and hdrs.get("authorization")
                and hdrs.get("x-goog-authuser") is not None
            ):
                captured.update(hdrs)

        context.on("request", _inspect_request)
        try:
            page.goto(_MUSIC_BASE, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass

        deadline = time.monotonic() + max(30, int(timeout_seconds))
        while not captured and time.monotonic() < deadline:
            try:
                if page.is_closed():
                    break
                page.wait_for_timeout(500)
            except Exception:
                break

        try:
            context.close()
        except Exception:
            pass

    if not captured:
        raise TimeoutError(
            "No detecté una sesión iniciada en YouTube Music. "
            "Inicia sesión en la ventana que se abre antes de cerrarla."
        )
    return captured


def _refresh_ytmusic_auth():
    """Authenticate YouTube Music.

    Tries (in order):
      1. ytmusicapi setup_oauth — proper OAuth device-code flow, browser opens
         automatically; ``input()`` is intercepted so it doesn't block a GUI app.
      2. Playwright browser-header capture — fallback for environments where
         setup_oauth is unavailable or fails.
    """
    with _YTMUSIC_AUTH_LOCK:
        try:
            from actions.auth_dialog import show_ytmusic_auth_pending_dialog
            show_ytmusic_auth_pending_dialog()
        except Exception:
            pass

        last_exc: Exception = RuntimeError("No se pudo iniciar el flujo de autenticación.")
        try:
            # --- Primary: ytmusicapi OAuth device-code flow ---
            from ytmusicapi import setup_oauth, YTMusic
            import builtins, io, contextlib

            # Intercept input() so the "Press Enter when done" prompt doesn't
            # block the GUI thread; the polling loop detects auth automatically.
            _orig_input = builtins.input

            def _noop_input(_prompt=""):
                return ""

            builtins.input = _noop_input
            try:
                # Suppress URL/code printed to stdout (not visible in GUI)
                with contextlib.redirect_stdout(io.StringIO()):
                    setup_oauth(filepath=str(OAUTH_FILE), open_browser=True)
            finally:
                builtins.input = _orig_input

            try:
                from actions.auth_dialog import close_ytmusic_auth_pending_dialog
                close_ytmusic_auth_pending_dialog()
            except Exception:
                pass
            return YTMusic(str(OAUTH_FILE))

        except Exception as exc:
            last_exc = exc

        try:
            # --- Fallback: Playwright browser-header capture ---
            headers = _capture_ytmusic_browser_headers()
            _save_ytmusic_headers(headers)
            from ytmusicapi import YTMusic

            try:
                from actions.auth_dialog import close_ytmusic_auth_pending_dialog
                close_ytmusic_auth_pending_dialog()
            except Exception:
                pass
            return YTMusic(str(OAUTH_FILE))

        except Exception as exc2:
            last_exc = exc2

        try:
            from actions.auth_dialog import close_ytmusic_auth_pending_dialog
            close_ytmusic_auth_pending_dialog()
        except Exception:
            pass
        raise PermissionError(
            "No pude completar el inicio de sesión de YouTube Music.\n"
            f"Detalle: {last_exc}"
        ) from last_exc


def _is_liked_songs_auth_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(
        needle in msg
        for needle in (
            "twoColumnBrowseResultsRenderer",
            "Sign in to listen to your liked tracks",
            "Looking for what you’ve liked?",
            "Looking for what you've liked?",
            "Please provide authentication before using this function",
        )
    )


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def search_songs(query: str, limit: int = 5) -> List[Dict]:
    yt = _get_ytmusic()
    results = yt.search(query, filter="songs", limit=limit)
    out = []
    for r in results[:limit]:
        out.append(_track_result(r))
    return out


def search_artists(query: str, limit: int = 5) -> List[Dict]:
    yt = _get_ytmusic()
    results = yt.search(query, filter="artists", limit=limit)
    out = []
    for r in results[:limit]:
        out.append({
            "browseId": r.get("browseId"),
            "name":     r.get("artist", r.get("name", "")),
            "url":      _artist_url(r["browseId"]) if r.get("browseId") else "",
            "thumbnail": _thumbnail_url(r),
        })
    return out


def search_albums(query: str, limit: int = 5) -> List[Dict]:
    yt = _get_ytmusic()
    results = yt.search(query, filter="albums", limit=limit)
    out = []
    for r in results[:limit]:
        out.append({
            "browseId": r.get("browseId"),
            "title":    r.get("title", ""),
            "artists":  _artist_names(r.get("artists")),
            "year":     r.get("year", ""),
            "url":      _album_url(r["browseId"]) if r.get("browseId") else "",
            "thumbnail": _thumbnail_url(r),
        })
    return out


def get_lyrics(query: str) -> str:
    """Search for a song and return its lyrics."""
    yt = _get_ytmusic()
    results = yt.search(query, filter="songs", limit=1)
    if not results:
        return f"No se encontró la canción '{query}'."
    song    = results[0]
    vid_id  = song.get("videoId")
    if not vid_id:
        return "No se pudo obtener el videoId de la canción."

    # Get watch playlist to extract lyrics browseId
    try:
        watch = yt.get_watch_playlist(vid_id)
        lyrics_id = watch.get("lyrics")
        if not lyrics_id:
            return f"No hay letra disponible para '{song.get('title')}'."
        lyrics_data = yt.get_lyrics(lyrics_id)
        lyrics_text = lyrics_data.get("lyrics", "")
        source      = lyrics_data.get("source", "")
        title       = song.get("title", query)
        artists     = _artist_names(song.get("artists"))
        header      = f"Letra de '{title}' — {artists}\n{'─'*40}\n"
        return header + (lyrics_text or "Sin letra.") + (f"\n\n[Fuente: {source}]" if source else "")
    except Exception as e:
        return f"Error obteniendo letra: {e}"


def get_artist_info(query: str) -> Dict:
    return get_artist_details(query=query)


def get_artist_details(query: str = "", browse_id: str = "") -> Dict:
    yt      = _get_ytmusic()
    if not browse_id:
        artists = yt.search(query, filter="artists", limit=1)
        if not artists:
            return {}
        browse_id = artists[0].get("browseId")
    if not browse_id:
        return {}
    info = yt.get_artist(browse_id)
    top_songs = []
    artist_name = info.get("name", "")
    for s in (info.get("songs", {}).get("results") or []):
        item = _track_result(s)
        if not item.get("artists"):
            item["artists"] = artist_name
        if item.get("videoId"):
            item["url"] = _song_url(item["videoId"])
            top_songs.append(item)

    def _release_items(section: str) -> List[Dict]:
        out = []
        for raw in (info.get(section, {}).get("results") or []):
            rid = raw.get("browseId") or ""
            out.append({
                "browseId": rid,
                "albumId": rid,
                "title": raw.get("title", ""),
                "artists": _artist_names(raw.get("artists")) or artist_name,
                "year": raw.get("year", ""),
                "type": raw.get("type", ""),
                "thumbnail": _thumbnail_url(raw, target_size=544),
                "url": _album_url(rid) if rid else "",
            })
        return out

    videos = []
    for raw in (info.get("videos", {}).get("results") or []):
        vid = raw.get("videoId") or ""
        if not vid:
            continue
        videos.append({
            "videoId": vid,
            "title": raw.get("title", ""),
            "artists": _artist_names(raw.get("artists")) or artist_name,
            "views": raw.get("views", ""),
            "thumbnail": _thumbnail_url(raw, target_size=800),
            "url": _song_url(vid),
        })

    related = []
    for raw in (info.get("related", {}).get("results") or []):
        rid = raw.get("browseId") or ""
        related.append({
            "browseId": rid,
            "name": raw.get("title") or raw.get("name") or "",
            "subscribers": raw.get("subscribers", ""),
            "thumbnail": _thumbnail_url(raw, target_size=544),
            "url": _artist_url(rid) if rid else "",
        })

    recommendations = []
    if top_songs:
        try:
            radio = yt.get_watch_playlist(videoId=top_songs[0]["videoId"], limit=20, radio=True)
            seen = {song.get("videoId") for song in top_songs}
            for raw in radio.get("tracks") or []:
                item = _track_result(raw)
                vid = item.get("videoId")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                item["url"] = _song_url(vid)
                recommendations.append(item)
                if len(recommendations) >= 10:
                    break
        except Exception:
            recommendations = []

    return {
        "name":       artist_name,
        "browseId":    browse_id,
        "channelId":    info.get("channelId", ""),
        "description": info.get("description", "") if info.get("description") else "",
        "subscribers": info.get("subscribers", ""),
        "monthlyListeners": info.get("monthlyListeners", ""),
        "views":       info.get("views", ""),
        "top_songs":  top_songs,
        "recommendations": recommendations,
        "albums":      _release_items("albums"),
        "singles":     _release_items("singles"),
        "videos":      videos,
        "related":     related,
        "url":        _artist_url(browse_id),
        "thumbnail":  _thumbnail_url(info, target_size=1200),
    }


def get_song_details(
    video_id: str = "",
    title: str = "",
    artists: str = "",
    album_id: str = "",
    artist_id: str = "",
) -> Dict:
    yt = _get_ytmusic()
    details: Dict = {
        "videoId": video_id or "",
        "title": title or "",
        "artists": artists or "",
        "albumId": album_id or "",
        "artistId": artist_id or "",
    }

    found = None
    if title and artists and not (video_id and album_id and artist_id):
        results = yt.search(f"{title} {artists}", filter="songs", limit=5)
        for r in results or []:
            if video_id and r.get("videoId") == video_id:
                found = r
                break
        if found is None and results:
            found = results[0]

    if found:
        track = _track_result(found)
        details.update({k: v for k, v in track.items() if v not in ("", None, [])})

    if video_id:
        try:
            song = yt.get_song(video_id)
            vd = song.get("videoDetails") or {}
            micro = (song.get("microformat") or {}).get("microformatDataRenderer") or {}
            details["title"] = details.get("title") or vd.get("title") or micro.get("title") or ""
            details["artists"] = details.get("artists") or vd.get("author") or micro.get("description") or ""
            details["duration"] = details.get("duration") or _fmt_duration(vd.get("lengthSeconds"))
            details["thumbnail"] = details.get("thumbnail") or _thumbnail_url(vd, target_size=1200) or _thumbnail_url(micro, target_size=1200)
            publish_date = str(micro.get("publishDate") or micro.get("uploadDate") or "")
            if publish_date[:4].isdigit():
                details.setdefault("year", publish_date[:4])
        except Exception:
            pass

    album_id = details.get("albumId") or ""
    if album_id:
        try:
            album = yt.get_album(album_id)
            details["album"] = album.get("title") or details.get("album", "")
            details["year"] = album.get("year") or details.get("year", "")
            details["albumThumbnail"] = _thumbnail_url(album, target_size=1200)
            if details.get("albumThumbnail"):
                details["thumbnail"] = details.get("albumThumbnail", "")
            if not details.get("artists"):
                details["artists"] = _artist_names(album.get("artists"))
        except Exception:
            pass

    artist_id = details.get("artistId") or ""
    artist_query = details.get("artists") or artists or ""
    try:
        artist = get_artist_details(query=artist_query, browse_id=artist_id)
        if artist:
            details["artistName"] = artist.get("name", artist_query)
            details["artistDescription"] = artist.get("description", "")
            details["artistThumbnail"] = _upgrade_thumbnail_url(artist.get("thumbnail", ""), 1200)
            details["artistBrowseId"] = artist_id or artist.get("browseId", "")
    except Exception:
        pass

    if details.get("thumbnail"):
        details["thumbnail"] = _upgrade_thumbnail_url(details.get("thumbnail", ""), 1200)
    if details.get("albumThumbnail"):
        details["albumThumbnail"] = _upgrade_thumbnail_url(details.get("albumThumbnail", ""), 1200)
    if details.get("artistThumbnail"):
        details["artistThumbnail"] = _upgrade_thumbnail_url(details.get("artistThumbnail", ""), 1200)

    external_cover = _itunes_cover_url(
        title=details.get("title", ""),
        artists=details.get("artists", ""),
        album=details.get("album", ""),
    )
    if external_cover:
        details["youtubeThumbnail"] = details.get("thumbnail", "")
        details["thumbnail"] = external_cover
        details["coverSource"] = "itunes"

    return details


def get_album_details(query: str = "", browse_id: str = "") -> Dict:
    yt = _get_ytmusic()
    if not browse_id:
        albums = yt.search(query, filter="albums", limit=1)
        if not albums:
            return {}
        browse_id = albums[0].get("browseId")
    if not browse_id:
        return {}
    info = yt.get_album(browse_id)
    album_title = info.get("title", "")
    album_artists = _artist_names(info.get("artists"))
    tracks = []
    for t in info.get("tracks") or []:
        item = _track_result(t)
        item["album"] = item.get("album") or album_title
        item["albumId"] = item.get("albumId") or browse_id
        item["artists"] = item.get("artists") or album_artists
        if item.get("videoId"):
            item["url"] = _song_url(item["videoId"])
            tracks.append(item)
    return {
        "browseId":    browse_id,
        "albumId":     browse_id,
        "title":       album_title,
        "artists":     album_artists,
        "year":        info.get("year", ""),
        "track_count": info.get("trackCount", len(tracks)),
        "description": (info.get("description") or "")[:200],
        "tracks":      tracks,
        "url":         _album_url(browse_id),
        "thumbnail":   _thumbnail_url(info, target_size=1200),
    }


def get_album_info(query: str) -> Dict:
    return get_album_details(query=query)


def get_liked_songs(limit: int | None = None) -> List[Dict]:
    lim = _optional_limit(limit)
    yt = _get_ytmusic(require_auth=True)
    try:
        data = yt.get_liked_songs(limit=lim)
    except Exception as exc:
        if _is_liked_songs_auth_error(exc):
            yt = _refresh_ytmusic_auth()
            data = yt.get_liked_songs(limit=lim)
        else:
            raise
    tracks = data.get("tracks") if isinstance(data, dict) else data
    out = []
    for t in tracks or []:
        if not isinstance(t, dict):
            continue
        vid = t.get("videoId")
        if not vid:
            continue
        item = _track_result(t)
        item["url"] = _song_url(vid)
        out.append(item)
    return out if lim is None else out[:lim]


def get_history(limit: int = 20) -> List[Dict]:
    yt      = _get_ytmusic(require_auth=True)
    history = yt.get_history()
    out     = []
    for t in history[:limit]:
        out.append(_track_result(t))
    return out


def like_song(query: str) -> str:
    yt      = _get_ytmusic(require_auth=True)
    results = yt.search(query, filter="songs", limit=1)
    if not results:
        return f"No se encontró '{query}'."
    vid_id = results[0].get("videoId")
    yt.rate_song(vid_id, "LIKE")
    title   = results[0].get("title", query)
    artists = _artist_names(results[0].get("artists"))
    return f"Le diste like a '{title}' — {artists}."


def get_song_like_status(video_id: str) -> bool:
    video_id = str(video_id or "").strip()
    if not video_id:
        return False
    yt = _get_ytmusic(require_auth=True)
    data = yt.get_watch_playlist(videoId=video_id, limit=1)
    for track in data.get("tracks") or []:
        if str(track.get("videoId") or "") == video_id:
            return str(track.get("likeStatus") or "").upper() == "LIKE"
    return False


def set_song_like(video_id: str, liked: bool) -> bool:
    video_id = str(video_id or "").strip()
    if not video_id:
        raise ValueError("No hay una canción activa.")
    yt = _get_ytmusic(require_auth=True)
    yt.rate_song(video_id, "LIKE" if liked else "INDIFFERENT")
    return bool(liked)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def ytmusic(parameters: dict, player=None, speak=None) -> str:
    """Dispatcher cleaned to prefer headless backend (mpv) and keep search/metadata helpers."""
    action = parameters.get("action", "play")
    query = parameters.get("query") or parameters.get("song") or ""

    try:
        # Prefer headless mpv backend when available
        try:
            from actions import ytmusic_headless as headless
        except Exception:
            headless = None

        # Playback controls
        if action == "pause":
            if headless:
                return "Pausado." if headless.pause() else "No se pudo pausar (mpv)."
            return "No hay backend headless disponible para pausar."

        if action in ("play_resume", "resume"):
            if headless:
                return "Reproduciendo." if headless.resume() else "No se pudo reanudar (mpv)."
            return "No hay backend headless disponible para reanudar."

        if action == "toggle_play":
            if headless:
                return "Alternando reproducción." if headless.toggle_play() else "No se pudo alternar (mpv)."
            return "No hay backend headless disponible para alternar reproducción."

        if action == "next":
            if headless:
                return headless.next()
            return "No hay backend headless disponible para saltar canción."

        if action == "previous":
            if headless:
                return headless.previous()
            return "No hay backend headless disponible para retroceder canción."

        if action == "like":
            return "Like no soportado en modo headless; usa ytmusicapi (liked_songs) para acciones personales."

        if action == "shuffle":
            if headless:
                return headless.show_queue() if parameters.get("show_queue") else "Usa play_playlist(..., shuffle=True) para mezclar listas."
            return "No hay backend headless disponible."

        if action == "volume":
            level = parameters.get("level", 50)
            try:
                level = max(0, min(100, int(level)))
            except (ValueError, TypeError):
                level = 50
            if headless:
                return "Volumen ajustado." if headless.volume(level) else "No se pudo ajustar volumen (mpv)."
            return "No hay backend headless disponible para ajustar volumen."

        if action == "current_song":
            if headless:
                info = headless.current()
                title = info.get("title", "")
                artists = info.get("artists", "")
                pos = info.get("position", 0)
                dur = info.get("duration", 0)
                return f"{title} — {artists} [{_fmt_duration(pos)} / {_fmt_duration(dur)}]"
            return "No hay backend headless disponible para obtener la canción actual."

        if action in ("list_playlist_tracks", "playlist_tracks"):
            query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
            limit = _optional_limit(parameters.get("limit"))
            shuffle = _as_bool(parameters.get("shuffle", False))
            return list_playlist_tracks(query_or_id=query_or_id, limit=limit, shuffle=shuffle)

        if action in ("playlist_names", "track_names"):
            tracks = parameters.get("tracks") or []
            if not tracks:
                query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
                limit = _optional_limit(parameters.get("limit"))
                tracks = list_playlist_tracks(query_or_id=query_or_id, limit=limit)
            return playlist_track_names(tracks)

        if action == "download_playlist_audio":
            query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
            limit = int(parameters.get("limit") or 1000)
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            shuffle = _as_bool(parameters.get("shuffle", False))
            quality = parameters.get("quality") or ""
            if not str(quality).strip():
                return "Antes de descargar la playlist, dime qué calidad quieres: baja, media, alta o best."
            files = download_playlist_audio(query_or_id=query_or_id, limit=limit, output_dir=output_dir, shuffle=shuffle, quality=quality)
            return f"Downloaded {len(files)} audio file(s)."

        if action == "download_audio_tracks":
            query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
            limit = int(parameters.get("limit") or 1000)
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            shuffle = _as_bool(parameters.get("shuffle", False))
            quality = parameters.get("quality") or ""
            if not str(quality).strip():
                return "Antes de descargar, dime qué calidad quieres: baja, media, alta o best."
            tracks = parameters.get("tracks")
            if not tracks:
                tracks = list_playlist_tracks(query_or_id=query_or_id, limit=limit, shuffle=shuffle)
            files = download_audio_tracks(tracks, output_dir=output_dir, quality=quality)
            return f"Downloaded {len(files)} audio file(s) to {output_dir or str(_downloads_dir('JARVIS_Audio'))}."

        if action in ("download_liked_audio", "download_liked_songs_audio"):
            limit = int(parameters.get("limit") or 25)
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            shuffle = _as_bool(parameters.get("shuffle", False))
            quality = parameters.get("quality") or ""
            if not str(quality).strip():
                return "Antes de descargar las canciones guardadas, dime qué calidad quieres: baja, media, alta o best."
            files = download_liked_audio(limit=limit, output_dir=output_dir, shuffle=shuffle, quality=quality)
            return f"Downloaded {len(files)} liked song(s) to {output_dir or str(_downloads_dir('JARVIS_Audio'))}."

        if action == "queue_playlist_download":
            query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            quality = parameters.get("quality") or _DOWNLOAD_STATE.get("audio_quality", "best")
            limit = int(parameters.get("limit") or 1000)
            shuffle = _as_bool(parameters.get("shuffle", False))
            return queue_playlist_download(
                query_or_id=query_or_id,
                limit=limit,
                output_dir=output_dir,
                shuffle=shuffle,
                quality=quality,
            )

        if action == "download_status":
            return download_status()

        if action == "download_status_verbose":
            return download_status_verbose()

        if action == "download_pause":
            return download_pause(parameters.get("enabled", parameters.get("paused", True)))

        if action in ("download_resume", "download_unpause"):
            return download_pause(False)

        if action == "download_cancel_all":
            return download_cancel_all()

        if action == "playlist_preview":
            query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
            limit = int(parameters.get("limit") or 5)
            return playlist_preview(query_or_id=query_or_id, limit=limit)

        if action in ("download_playlist_range", "download_selected_range"):
            query_or_id = parameters.get("query") or parameters.get("playlist") or parameters.get("playlist_id") or ""
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            quality = parameters.get("quality") or _DOWNLOAD_STATE.get("audio_quality", "best")
            start = int(parameters.get("start") or 1)
            end = int(parameters.get("end") or parameters.get("limit") or start)
            files = download_playlist_range(
                query_or_id=query_or_id,
                start=start,
                end=end,
                output_dir=output_dir,
                quality=quality,
            )
            return f"Downloaded {len(files)} selected audio file(s)."

        if action == "download_resume_failed":
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            return download_resume_failed(output_dir=output_dir)

        if action == "open_download_folder":
            kind = parameters.get("kind", "audio")
            return open_download_folder(kind)

        if action == "retry_failed_downloads":
            output_dir = parameters.get("output_dir") or parameters.get("path") or ""
            return retry_failed_downloads(output_dir=output_dir)

        if action == "cleanup_partial_downloads":
            kind = parameters.get("kind", "audio")
            return cleanup_partial_downloads(kind)

        if action == "set_default_quality":
            return set_default_quality(
                audio=parameters.get("audio_quality") or parameters.get("quality") or "",
                video=parameters.get("video_quality") or "",
            )

        # Play
        if action == "play":
            search_type = parameters.get("type", "song").lower()
            if not query:
                return "Necesito saber qué quieres reproducir."

            if search_type in ("artist", "artista"):
                # open artist page in browser as lightweight fallback
                artists = search_artists(query, limit=1)
                if not artists:
                    return f"No se encontró el artista '{query}'."
                a = artists[0]
                url = _artist_url(a.get("browseId", ""))
                try:
                    import webbrowser
                    webbrowser.open(url)
                except Exception:
                    pass
                return f"Abriendo artista '{a.get('name','')}' en el navegador."

            if search_type in ("album", "álbum"):
                albums = search_albums(query, limit=1)
                if not albums:
                    return f"No se encontró el álbum '{query}'."
                alb = albums[0]
                url = _album_url(alb.get("browseId", ""))
                try:
                    import webbrowser
                    webbrowser.open(url)
                except Exception:
                    pass
                return f"Abriendo álbum '{alb.get('title','')}' en el navegador."

            # default: song
            songs = search_songs(query, limit=1)
            if not songs or not songs[0].get("videoId"):
                return f"No se encontró '{query}'."
            s = songs[0]
            vid = s["videoId"]
            if headless:
                return headless.play(query)
            # fallback: open in browser
            try:
                import webbrowser
                webbrowser.open(_song_url(vid))
            except Exception:
                pass
            return f"Abriendo '{s.get('title','')}' en el navegador."

        # ── SEARCH ───────────────────────────────────────────────────────────
        elif action == "search":
            search_type = parameters.get("type", "song").lower()
            limit       = int(parameters.get("limit") or 5)
            if not query:
                return "Necesito un término de búsqueda."

            if search_type in ("artist", "artista"):
                results = search_artists(query, limit)
                if not results:
                    return f"No se encontraron artistas para '{query}'."
                lines = [f"Artistas para '{query}':"]
                for r in results:
                    lines.append(f"- {r['name']}")
                return "\n".join(lines)

            elif search_type in ("album", "álbum"):
                results = search_albums(query, limit)
                if not results:
                    return f"No se encontraron álbumes para '{query}'."
                lines = [f"Álbumes para '{query}':"]
                for r in results:
                    lines.append(f"- {r['title']} — {r['artists']} ({r['year']})")
                return "\n".join(lines)

            else:  # songs
                results = search_songs(query, limit)
                if not results:
                    return f"No se encontraron canciones para '{query}'."
                lines = [f"Canciones para '{query}':"]
                for s in results:
                    lines.append(f"- {s['title']} — {s['artists']} [{s['duration']}]")
                return "\n".join(lines)

        # ── LYRICS ───────────────────────────────────────────────────────────
        elif action == "lyrics":
            if not query:
                return "Dime el nombre de la canción."
            return get_lyrics(query)

        # ── ARTIST INFO ──────────────────────────────────────────────────────
        elif action == "artist_info":
            if not query:
                return "Dime el nombre del artista."
            info = get_artist_info(query)
            if not info:
                return f"No se encontró información sobre '{query}'."
            lines = [
                f"🎤 {info['name']}",
                f"Suscriptores: {info.get('subscribers', 'N/A')}",
            ]
            if info.get("description"):
                lines.append(info["description"])
            if info.get("top_songs"):
                lines.append("\nTop canciones:")
                for s in info["top_songs"]:
                    lines.append(f"  - {s['title']}")
            return "\n".join(lines)

        # ── ALBUM INFO ───────────────────────────────────────────────────────
        elif action == "album_info":
            if not query:
                return "Dime el nombre del álbum."
            info = get_album_info(query)
            if not info:
                return f"No se encontró información sobre '{query}'."
            lines = [
                f"💿 {info['title']} — {info['artists']} ({info['year']})",
                f"{info['track_count']} canciones",
            ]
            if info.get("description"):
                lines.append(info["description"])
            lines.append("\nCanciones:")
            for t in info["tracks"]:
                dur = f" [{t['duration']}]" if t.get("duration") else ""
                lines.append(f"  - {t['title']}{dur}")
            return "\n".join(lines)

        # ── LIKED SONGS ──────────────────────────────────────────────────────
        elif action == "liked_songs":
            limit = int(parameters.get("limit") or 25)
            songs = get_liked_songs(limit)
            if not songs:
                return "No tienes canciones con like o no hay autenticación."
            lines = [f"{len(songs)} canciones con like:"]
            for s in songs:
                lines.append(f"- {s['title']} — {s['artists']}")
            return "\n".join(lines)

        # ── HISTORY ──────────────────────────────────────────────────────────
        elif action == "history":
            limit   = int(parameters.get("limit") or 20)
            history = get_history(limit)
            if not history:
                return "Sin historial o sin autenticación."
            lines = [f"Últimas {len(history)} reproducciones:"]
            for s in history:
                lines.append(f"- {s['title']} — {s['artists']}")
            return "\n".join(lines)

        # ── LIKE SONG (by search) ─────────────────────────────────────────────
        elif action == "like_song":
            if not query:
                return "Dime el nombre de la canción."
            return like_song(query)

        else:
            return (
                f"Acción desconocida: {action}. "
                "Usa: play, pause, play_resume, toggle_play, next, previous, "
                "volume, current_song, shuffle, like, search, lyrics, "
                "artist_info, album_info, liked_songs, history, like_song, "
                "list_playlist_tracks, playlist_names, download_playlist_audio, download_liked_audio."
            )

    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"YouTube Music error: {e}"


# ---------------------------------------------------------------------------
# Playlist export / import
# ---------------------------------------------------------------------------

_PLAYLIST_FORMAT_VERSION = 1


def _track_to_export(t: Dict) -> Dict:
    """Normalize a track dict to a portable export record."""
    video_id = (
        t.get("videoId") or t.get("video_id") or t.get("id") or ""
    )
    artists_raw = t.get("artists", "")
    if isinstance(artists_raw, list):
        artists_list = [
            (a.get("name") or a.get("artist") or str(a)) if isinstance(a, dict) else str(a)
            for a in artists_raw
            if a
        ]
    else:
        artists_list = [str(artists_raw)] if artists_raw else []

    return {
        "title": t.get("title", ""),
        "artists": artists_list,
        "video_id": video_id,
        "duration_seconds": int(t.get("duration_seconds") or t.get("duration") or 0),
        "album": t.get("album", ""),
        "is_video": bool(t.get("isVideo") or t.get("is_video")),
    }


def export_liked_to_file(output_path: str, limit: Optional[int] = None) -> Dict:
    """Fetch liked songs and save them to *output_path* as a Jarvis playlist JSON.

    Returns a summary dict with ``name``, ``count`` and ``path``.
    """
    songs = get_liked_songs(limit=limit)
    tracks = [_track_to_export(t) for t in songs]
    payload = {
        "jarvis_playlist": True,
        "version": _PLAYLIST_FORMAT_VERSION,
        "name": "Mis Me Gusta",
        "type": "liked",
        "exported_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "tracks": tracks,
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"name": payload["name"], "count": len(tracks), "path": str(out)}


def export_playlist_to_file(query_or_id: str, output_path: str) -> Dict:
    """Fetch a playlist by name or ID and save it to *output_path*.

    Returns a summary dict with ``name``, ``count`` and ``path``.
    """
    tracks_raw = list_playlist_tracks(query_or_id=query_or_id, limit=None, shuffle=False)
    if not tracks_raw:
        raise ValueError(f"No se encontró la playlist '{query_or_id}'.")
    tracks = [_track_to_export(t) for t in tracks_raw]

    # Try to get a nice name for the playlist
    name = query_or_id
    try:
        yt = _get_ytmusic()
        pls = yt.get_library_playlists(limit=50) or []
        for pl in pls:
            if pl.get("playlistId") == query_or_id or (
                pl.get("title", "").lower() == query_or_id.lower()
            ):
                name = pl.get("title", query_or_id)
                break
    except Exception:
        pass

    payload = {
        "jarvis_playlist": True,
        "version": _PLAYLIST_FORMAT_VERSION,
        "name": name,
        "type": "playlist",
        "exported_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "tracks": tracks,
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"name": name, "count": len(tracks), "path": str(out)}


def import_playlist_from_file(file_path: str) -> List[Dict]:
    """Load a Jarvis playlist JSON and return a list of track dicts.

    Each dict has keys: ``videoId``, ``title``, ``artists`` (str), ``is_video``.
    Tracks without a ``video_id`` are silently skipped.
    """
    data = json.loads(Path(file_path).read_text(encoding="utf-8"))
    if not data.get("jarvis_playlist"):
        raise ValueError("El archivo no es una playlist exportada por Jarvis.")

    tracks = []
    for t in data.get("tracks", []):
        vid = t.get("video_id") or t.get("videoId") or ""
        if not vid:
            continue
        artists_raw = t.get("artists", "")
        if isinstance(artists_raw, list):
            artists_str = ", ".join(str(a) for a in artists_raw if a)
        else:
            artists_str = str(artists_raw)
        tracks.append({
            "videoId": vid,
            "title": t.get("title", ""),
            "artists": artists_str,
            "is_video": bool(t.get("is_video")),
        })
    return tracks
