"""Tagging and container normalization for downloaded music files.

yt-dlp's ``bestaudio`` picks whatever stream YouTube ranks highest, which on
music tracks is usually WebM/Opus — a container most phones (and every iOS
device) refuse to play, with no tags of any kind. This module handles the two
fixes for that:

* :func:`normalize_container` turns a non-portable download into ``.m4a``
  (MP4/AAC) when ffmpeg is available. Downloads that already asked for m4a skip
  the re-encode entirely.
* :func:`tag_audio_file` writes title/artist/album/genre/track/year plus
  embedded cover art, so the file shows up correctly in any phone music app.

Tags are written with mutagen (pure Python, no ffmpeg needed). Cover art is
fetched from the track thumbnail and re-encoded to JPEG with Pillow when the
source is WebP, which MP4 and ID3 readers do not understand.

Everything degrades quietly: a missing mutagen, a dead cover URL or an
unsupported container leaves the audio file untouched instead of failing the
download.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import mutagen  # noqa: F401
    _MUTAGEN_OK = True
except Exception:
    _MUTAGEN_OK = False

_MUTAGEN_WARNED = False

# Containers a phone can be trusted to play. Anything else is a conversion
# candidate whenever ffmpeg is around.
PORTABLE_EXTS = {".m4a", ".mp3", ".aac", ".flac", ".wav"}

# WebM is a Matroska container: mutagen.File() returns None for it, so there is
# no way to tag one in place. The only fix is converting it, which needs ffmpeg.
UNTAGGABLE_EXTS = {".webm", ".mkv"}

AUDIO_EXTS = PORTABLE_EXTS | {".opus", ".ogg", ".oga"} | UNTAGGABLE_EXTS

# Scratch name for an encode in flight. It has to end in .m4a for the muxer to
# be picked, which means a killed run leaves something a later scan would
# happily treat as a real track — so the suffix is recognizable and swept.
_TEMP_SUFFIX = ".converting.m4a"

_USER_AGENT = "JARVIS/1.0"
_COVER_TIMEOUT = 6
_ITUNES_TIMEOUT = 4


# ---------------------------------------------------------------------------
# Transcoding backends / container
# ---------------------------------------------------------------------------

def _bundled(*names: str) -> str:
    """Locate a helper executable shipped with the app.

    ``parent.parent`` is the repo root when running from source and ``_internal``
    in the PyInstaller build — which is where the spec drops mpv.exe and friends,
    so one candidate list covers both.
    """
    root = Path(__file__).resolve().parent.parent
    for name in names:
        for candidate in (root / name, root / "tools" / name, root / "tools" / Path(name).stem / name):
            if candidate.is_file():
                return str(candidate)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def ffmpeg_path() -> str:
    """Return an ffmpeg executable path, preferring one shipped next to the app."""
    return _bundled("ffmpeg.exe", "ffmpeg")


def mpv_path() -> str:
    """Return the mpv that already ships with Jarvis for playback.

    mpv links the same libavcodec/libavformat ffmpeg does and can encode through
    ``--o=``, so the app can convert audio without anyone installing ffmpeg.
    """
    return _bundled("mpv.exe", "mpv")


def transcoder() -> tuple[str, str]:
    """Return ``(kind, path)`` for the available converter: ffmpeg, mpv or none."""
    exe = ffmpeg_path()
    if exe:
        return "ffmpeg", exe
    exe = mpv_path()
    if exe:
        return "mpv", exe
    return "", ""


def is_portable(path) -> bool:
    return Path(path).suffix.lower() in PORTABLE_EXTS


def _transcode_command(kind: str, exe: str, src: Path, dst: Path, bitrate: str) -> list[str]:
    if kind == "ffmpeg":
        return [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                "-vn", "-c:a", "aac", "-b:a", str(bitrate), str(dst)]
    # mpv picks the muxer from the output extension, hence the .m4a temp name.
    # --no-config keeps the user's playback profile (equalizer, audio device)
    # out of an encode that must stay a plain transcode.
    return [exe, str(src), "--no-config", "--no-terminal", "--vid=no",
            f"--o={dst}", "--oac=aac", f"--oacopts=b={bitrate}"]


def normalize_container(path, *, bitrate: str = "192k") -> str:
    """Convert ``path`` to .m4a when it is in a container phones choke on.

    Returns the resulting path — the original one when no conversion was needed
    or possible, so callers can always use the return value.
    """
    src = Path(path)
    if not src.is_file() or is_portable(src):
        return str(src)

    kind, exe = transcoder()
    if not kind:
        return str(src)

    dst = src.with_suffix(".m4a")
    # Both encoders choose the muxer by extension, so the scratch file has to
    # keep the .m4a suffix — a ".part" name makes them fail to pick one.
    tmp = src.with_name(f"{src.stem}{_TEMP_SUFFIX}")
    try:
        # Opus/Vorbis can't be copied into MP4, so this is a real re-encode.
        proc = subprocess.run(
            _transcode_command(kind, exe, src, tmp, bitrate),
            capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return str(src)
        tmp.replace(dst)
        if dst != src:
            src.unlink(missing_ok=True)
        return str(dst)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return str(src)


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------

def fetch_cover(url: str) -> tuple[bytes, str]:
    """Download cover art and return ``(bytes, mime)``, or ``(b"", "")``."""
    url = str(url or "").strip()
    if not url:
        return b"", ""
    try:
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=_COVER_TIMEOUT) as resp:
            data = resp.read()
            mime = str(resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except Exception:
        return b"", ""
    if not data:
        return b"", ""
    return _as_jpeg_or_png(data, mime)


def _as_jpeg_or_png(data: bytes, mime: str) -> tuple[bytes, str]:
    """Force cover bytes into JPEG/PNG — the only formats MP4 and ID3 accept."""
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    # WebP (what googleusercontent serves by default) and anything else needs a
    # transcode; without Pillow we'd rather ship no cover than a broken one.
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return b"", ""


# ---------------------------------------------------------------------------
# Metadata lookup
# ---------------------------------------------------------------------------

def lookup_itunes(title: str = "", artists: str = "", album: str = "") -> dict:
    """Best-effort iTunes lookup for the fields YouTube never provides (genre)."""
    title = str(title or "").strip()
    artists = str(artists or "").strip()
    album = str(album or "").strip()
    if not title and not album:
        return {}
    term = " ".join(part for part in (title, artists, album) if part)
    url = "https://itunes.apple.com/search?" + urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": 8}
    )
    try:
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=_ITUNES_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return {}

    best: dict = {}
    best_score = 0
    for item in payload.get("results") or []:
        score = _match_score(title, item.get("trackName", "")) + _match_score(artists, item.get("artistName", ""))
        if album:
            score += _match_score(album, item.get("collectionName", ""))
        if score > best_score:
            best_score = score
            best = item
    if best_score < 4 or not best:
        return {}

    art = str(best.get("artworkUrl100") or best.get("artworkUrl60") or "")
    return {
        "genre": str(best.get("primaryGenreName") or ""),
        "album": str(best.get("collectionName") or ""),
        "year": str(best.get("releaseDate") or "")[:4],
        "track_number": int(best.get("trackNumber") or 0),
        "track_total": int(best.get("trackCount") or 0),
        "disc_number": int(best.get("discNumber") or 0),
        "cover": re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", art) if art else "",
    }


def lookup_youtube(video_id: str) -> dict:
    """Resolve title/artist/cover from a videoId through the YouTube Music API.

    Old downloads are named ``NNN - Title [videoId].ext`` — no artist anywhere in
    the name — so the id is the only reliable handle on what the track actually
    is. Imported lazily: actions.ytmusic imports this module.
    """
    vid = str(video_id or "").strip()
    if not vid:
        return {}
    try:
        from actions.ytmusic import _get_ytmusic, _upgrade_thumbnail_url

        data = _get_ytmusic(require_auth=False).get_song(vid) or {}
    except Exception:
        return {}

    details = data.get("videoDetails") or {}
    thumbs = (details.get("thumbnail") or {}).get("thumbnails") or []
    cover = ""
    if thumbs:
        best = max(thumbs, key=lambda t: int(t.get("width") or 0) * int(t.get("height") or 0))
        try:
            cover = _upgrade_thumbnail_url(str(best.get("url") or ""), 1200)
        except Exception:
            cover = str(best.get("url") or "")
    author = re.sub(r"\s*-\s*Topic$", "", str(details.get("author") or "")).strip()
    return {
        "title": str(details.get("title") or "").strip(),
        "artists": author,
        "thumbnail": cover,
        "videoId": vid,
    }


def _match_score(expected: str, candidate: str) -> int:
    exp = _norm(expected)
    cand = _norm(candidate)
    if not exp or not cand:
        return 0
    if exp == cand:
        return 3
    if exp in cand or cand in exp:
        return 2
    return 0


def _norm(text: str) -> str:
    import unicodedata

    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9]+", " ", raw.lower())
    return re.sub(r"\s+", " ", raw).strip()


def build_metadata(track: Optional[dict] = None, info: Optional[dict] = None, *, lookup: bool = True) -> dict:
    """Merge the YouTube Music track dict, yt-dlp's info dict and iTunes.

    Precedence is track dict > yt-dlp info > iTunes: the first two describe the
    exact song that was downloaded, iTunes only fills the gaps (genre, mostly).
    """
    track = track if isinstance(track, dict) else {}
    info = info if isinstance(info, dict) else {}

    title = str(track.get("title") or info.get("track") or info.get("title") or "").strip()
    artists = str(track.get("artists") or info.get("artist") or info.get("uploader") or "").strip()
    # YouTube appends " - Topic" to auto-generated artist channels.
    artists = re.sub(r"\s*-\s*Topic$", "", artists).strip()
    # Video titles repeat the artist ("The Beatles - Blackbird"); once the artist
    # has its own tag that prefix is noise in every player's track list.
    if artists and title.lower().startswith(f"{artists.lower()} - "):
        title = title[len(artists) + 3:].strip() or title
    album = str(track.get("album") or info.get("album") or "").strip()
    genre = str(info.get("genre") or "").strip()
    year = str(info.get("release_year") or "")[:4] or str(info.get("upload_date") or "")[:4]
    cover = str(track.get("thumbnail") or info.get("thumbnail") or "").strip()
    video_id = str(track.get("videoId") or info.get("id") or "").strip()

    meta = {
        "title": title,
        "artists": artists,
        "album": album,
        "genre": genre,
        "year": year,
        "track_number": 0,
        "track_total": 0,
        "disc_number": 0,
        "cover_url": cover,
        # Only a real id earns a comment; an empty one would write a dead URL.
        "comment": f"https://music.youtube.com/watch?v={video_id}" if video_id else "",
    }

    needs_lookup = lookup and (not genre or not album or not cover)
    if needs_lookup:
        extra = lookup_itunes(title, artists, album)
        for key, target in (("genre", "genre"), ("album", "album"), ("year", "year")):
            if not meta[target] and extra.get(key):
                meta[target] = extra[key]
        for key in ("track_number", "track_total", "disc_number"):
            if not meta[key] and extra.get(key):
                meta[key] = extra[key]
        if not meta["cover_url"] and extra.get("cover"):
            meta["cover_url"] = extra["cover"]

    return meta


# ---------------------------------------------------------------------------
# Tag writing
# ---------------------------------------------------------------------------

def mutagen_available() -> bool:
    return _MUTAGEN_OK


def tag_audio_file(path, meta: dict, *, cover: bytes = b"", cover_mime: str = "") -> bool:
    """Write ``meta`` into the file at ``path``. Returns True when tags landed."""
    if not _MUTAGEN_OK:
        global _MUTAGEN_WARNED
        if not _MUTAGEN_WARNED:
            _MUTAGEN_WARNED = True
            print(f"[JARVIS] ⚠️ Sin mutagen en {sys.executable}: las descargas se guardan sin etiquetas.")
        return False
    target = Path(path)
    if not target.is_file():
        return False

    if not cover and meta.get("cover_url"):
        cover, cover_mime = fetch_cover(meta["cover_url"])

    ext = target.suffix.lower()
    try:
        if ext in (".m4a", ".mp4", ".m4b"):
            return _tag_mp4(target, meta, cover, cover_mime)
        if ext == ".mp3":
            return _tag_mp3(target, meta, cover, cover_mime)
        if ext == ".flac":
            return _tag_flac(target, meta, cover, cover_mime)
        if ext in (".opus", ".ogg", ".oga"):
            return _tag_vorbis(target, meta, cover, cover_mime)
    except Exception:
        return False
    return False


def _artist_list(meta: dict) -> list[str]:
    artists = str(meta.get("artists") or "").strip()
    return [artists] if artists else []


def _tag_mp4(target: Path, meta: dict, cover: bytes, cover_mime: str) -> bool:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(str(target))
    tags = audio.tags
    if tags is None:
        audio.add_tags()
        tags = audio.tags

    if meta.get("title"):
        tags["\xa9nam"] = [meta["title"]]
    if meta.get("artists"):
        tags["\xa9ART"] = [meta["artists"]]
        tags["aART"] = [meta["artists"].split(",")[0].strip()]
    if meta.get("album"):
        tags["\xa9alb"] = [meta["album"]]
    if meta.get("genre"):
        tags["\xa9gen"] = [meta["genre"]]
    if meta.get("year"):
        tags["\xa9day"] = [str(meta["year"])]
    if meta.get("comment"):
        tags["\xa9cmt"] = [meta["comment"]]
    if meta.get("track_number"):
        tags["trkn"] = [(int(meta["track_number"]), int(meta.get("track_total") or 0))]
    if meta.get("disc_number"):
        tags["disk"] = [(int(meta["disc_number"]), 0)]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover, imageformat=fmt)]

    audio.save()
    return True


def _tag_mp3(target: Path, meta: dict, cover: bytes, cover_mime: str) -> bool:
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
    from mutagen.mp3 import MP3

    audio = MP3(str(target), ID3=ID3)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    if meta.get("title"):
        tags.setall("TIT2", [TIT2(encoding=3, text=meta["title"])])
    if meta.get("artists"):
        tags.setall("TPE1", [TPE1(encoding=3, text=meta["artists"])])
        tags.setall("TPE2", [TPE2(encoding=3, text=meta["artists"].split(",")[0].strip())])
    if meta.get("album"):
        tags.setall("TALB", [TALB(encoding=3, text=meta["album"])])
    if meta.get("genre"):
        tags.setall("TCON", [TCON(encoding=3, text=meta["genre"])])
    if meta.get("year"):
        tags.setall("TDRC", [TDRC(encoding=3, text=str(meta["year"]))])
    if meta.get("track_number"):
        num = str(meta["track_number"])
        if meta.get("track_total"):
            num = f"{num}/{meta['track_total']}"
        tags.setall("TRCK", [TRCK(encoding=3, text=num)])
    if cover:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=cover_mime or "image/jpeg", type=3, desc="Cover", data=cover))

    audio.save()
    return True


def _tag_flac(target: Path, meta: dict, cover: bytes, cover_mime: str) -> bool:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(str(target))
    _fill_vorbis_comments(audio, meta)
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = cover_mime or "image/jpeg"
        pic.desc = "Cover"
        pic.data = cover
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save()
    return True


def _tag_vorbis(target: Path, meta: dict, cover: bytes, cover_mime: str) -> bool:
    import base64

    from mutagen import File as MutagenFile
    from mutagen.flac import Picture

    audio = MutagenFile(str(target))
    if audio is None:
        return False
    if audio.tags is None:
        audio.add_tags()
    _fill_vorbis_comments(audio, meta)
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = cover_mime or "image/jpeg"
        pic.desc = "Cover"
        pic.data = cover
        audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()
    return True


def _fill_vorbis_comments(audio, meta: dict) -> None:
    if meta.get("title"):
        audio["title"] = [meta["title"]]
    if meta.get("artists"):
        audio["artist"] = [meta["artists"]]
        audio["albumartist"] = [meta["artists"].split(",")[0].strip()]
    if meta.get("album"):
        audio["album"] = [meta["album"]]
    if meta.get("genre"):
        audio["genre"] = [meta["genre"]]
    if meta.get("year"):
        audio["date"] = [str(meta["year"])]
    if meta.get("track_number"):
        audio["tracknumber"] = [str(meta["track_number"])]
    if meta.get("track_total"):
        audio["tracktotal"] = [str(meta["track_total"])]


# ---------------------------------------------------------------------------
# High level entry points
# ---------------------------------------------------------------------------

def finalize_download(path, track: Optional[dict] = None, info: Optional[dict] = None,
                      *, convert: bool = True, lookup: bool = True) -> str:
    """Normalize the container and write tags for a freshly downloaded file."""
    return finalize_file(path, track, info, convert=convert, lookup=lookup)["path"]


def finalize_file(path, track: Optional[dict] = None, info: Optional[dict] = None,
                  *, convert: bool = True, lookup: bool = True) -> dict:
    """Same as :func:`finalize_download` but reports what actually happened.

    Returns ``{"path", "converted", "tagged"}``. ``tagged`` is the real result of
    the write, not the attempt: a WebM that ffmpeg could not convert stays
    untaggable, and callers must not report it as repaired.
    """
    final = str(path or "")
    if not final:
        return {"path": final, "converted": False, "tagged": False}

    before = Path(final).suffix.lower()
    if convert:
        final = normalize_container(final)
    converted = Path(final).suffix.lower() != before

    tagged = False
    try:
        meta = build_metadata(track, info, lookup=lookup)
        tagged = tag_audio_file(final, meta)
    except Exception:
        tagged = False
    return {"path": final, "converted": converted, "tagged": bool(tagged)}


def already_tagged(path) -> bool:
    """True when the file is portable and already carries title, artist and cover.

    Lets a re-run (or a resumed run after a cancel) skip finished files without
    paying for a YouTube Music call, an iTunes call and a cover download each.
    """
    if not _MUTAGEN_OK:
        return False
    target = Path(path)
    ext = target.suffix.lower()
    if ext in UNTAGGABLE_EXTS or not target.is_file():
        return False
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(target))
        if audio is None or not audio.tags:
            return False
        tags = audio.tags
        if ext in (".m4a", ".mp4", ".m4b"):
            keys = ("\xa9nam", "\xa9ART", "covr")
        elif ext == ".mp3":
            return bool(tags.getall("TIT2") and tags.getall("TPE1") and tags.getall("APIC"))
        else:
            keys = ("title", "artist", "metadata_block_picture")
            if ext == ".flac":
                return bool(tags.get("title") and tags.get("artist") and audio.pictures)
        return all(tags.get(key) for key in keys)
    except Exception:
        return False


def retag_file(path, *, convert: bool = True, lookup: bool = True, force: bool = False) -> dict:
    """Repair an already-downloaded file, deriving metadata from its name.

    Existing downloads are named ``NNN - Artist - Title [videoId].ext``, which is
    enough to look the track up and rebuild proper tags.
    """
    src = Path(path)
    if not src.is_file():
        return {"path": str(src), "converted": False, "tagged": False, "skipped": False}
    if not force and already_tagged(src):
        return {"path": str(src), "converted": False, "tagged": True, "skipped": True}
    stem = src.stem
    id_match = re.search(r"\[([A-Za-z0-9_-]{11})\]\s*$", stem)
    video_id = id_match.group(1) if id_match else ""
    stem = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]\s*$", "", stem)
    stem = re.sub(r"^\d{1,3}\s*-\s*", "", stem).strip()
    artists, _, title = stem.partition(" - ")
    if not title:
        title, artists = stem, ""

    track = {"title": title.strip(), "artists": artists.strip(), "videoId": video_id}
    # The API knows the real artist and cover; the filename usually doesn't.
    if video_id and lookup:
        for key, value in lookup_youtube(video_id).items():
            if value:
                track[key] = value

    return finalize_file(src, track, None, convert=convert, lookup=lookup)


def _emit(progress_hook, *, active: bool, percent: float, label: str, detail: str,
          can_cancel: bool = True) -> None:
    if not progress_hook:
        return
    try:
        progress_hook({"active": active, "percent": percent, "label": label,
                       "detail": detail, "can_cancel": can_cancel})
    except Exception:
        pass


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}min"
    if seconds >= 60:
        return f"{seconds // 60}min"
    return f"{seconds}s"


def retag_directory(directory, *, convert: bool = True, lookup: bool = True, force: bool = False,
                    progress_hook=None, cancel_event=None, max_workers: int = 4) -> dict:
    """Retag (and optionally convert) every audio file in a folder.

    ``tagged`` counts files that really carry tags now; ``untaggable`` counts the
    WebM downloads that stayed WebM because no encoder could convert them.

    Work runs on a small thread pool: a whole library is thousands of files and
    each one costs an iTunes round-trip plus (for WebM) a re-encode, so doing it
    one at a time takes hours. Unlike the downloader there is no remote rate
    limit to respect here beyond the iTunes lookup, hence the wider pool.

    A run over a full library takes over an hour, so it reports progress after
    every file and honours ``cancel_event`` between files — an encode in flight
    is left to finish, which costs a few seconds at most.
    """
    base = Path(directory).expanduser()
    if not base.is_dir():
        return {"ok": False, "error": f"No existe la carpeta: {base}"}
    if not _MUTAGEN_OK:
        # Without mutagen every write silently no-ops while conversions still
        # run, which looks like success and leaves a library of blank files.
        return {"ok": False, "error": (
            "Falta la librería mutagen en el entorno que ejecuta Jarvis "
            f"({sys.executable}); sin ella no se puede escribir ninguna etiqueta. "
            "Instálala con: pip install mutagen"
        )}

    _emit(progress_hook, active=True, percent=0.0, label="Buscando archivos",
          detail=str(base))
    # A leftover scratch file means its encode was killed, so it is always an
    # incomplete copy of a track that is either still there as .webm or already
    # converted. Sweeping them first keeps them out of the scan below.
    swept = 0
    for stale in base.rglob(f"*{_TEMP_SUFFIX}"):
        try:
            stale.unlink()
            swept += 1
        except Exception:
            pass

    files = [p for p in sorted(base.rglob("*"))
             if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.endswith(_TEMP_SUFFIX)]
    total = len(files)
    if not total:
        _emit(progress_hook, active=False, percent=100.0, label="Nada que reparar",
              detail=str(base), can_cancel=False)
        return {"ok": True, "files": 0, "tagged": 0, "converted": 0, "untaggable": 0,
                "skipped": 0, "swept": swept, "cancelled": False, "transcoder": transcoder()[0]}

    _emit(progress_hook, active=True, percent=0.0, label="Reparando metadatos",
          detail=f"0/{total} · preparando")

    converted = 0
    tagged = 0
    untaggable = 0
    done = 0
    skipped = 0
    worked = 0
    lock = threading.Lock()
    started = time.monotonic()

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _one(path: Path) -> None:
        nonlocal converted, tagged, untaggable, done, skipped, worked
        if _cancelled():
            return
        result = retag_file(path, convert=convert, lookup=lookup, force=force)
        with lock:
            converted += int(result["converted"])
            tagged += int(result["tagged"])
            if result.get("skipped"):
                skipped += 1
            else:
                worked += 1
            if not result["tagged"] and Path(result["path"]).suffix.lower() in UNTAGGABLE_EXTS:
                untaggable += 1
            done += 1
            position = done
            processed = worked
        detail = f"{position}/{total} · {path.name}"
        # Skipped files finish instantly, so the ETA is estimated from the ones
        # that actually did work — otherwise a resumed run reports minutes left
        # while it races through hundreds of already-repaired tracks.
        if processed >= 3:
            per_file = (time.monotonic() - started) / processed
            detail = f"{detail} · quedan {_fmt_eta(per_file * (total - position))}"
        _emit(progress_hook, active=True, percent=(position / total) * 100.0,
              label="Reparando metadatos", detail=detail)

    workers = max(1, min(int(max_workers or 1), total))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in [pool.submit(_one, path) for path in files]:
            try:
                fut.result()
            except Exception:
                pass

    cancelled = _cancelled()
    _emit(progress_hook, active=False,
          percent=(done / total) * 100.0 if cancelled else 100.0,
          label="Reparación cancelada" if cancelled else "Metadatos listos",
          detail=f"{tagged} archivo(s) etiquetado(s)", can_cancel=False)
    return {"ok": True, "files": total, "tagged": tagged, "converted": converted,
            "untaggable": untaggable, "skipped": skipped, "swept": swept,
            "cancelled": cancelled, "transcoder": transcoder()[0]}
