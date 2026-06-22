#youtube_video.py
import json
import re
import sys
import time
import subprocess
import shutil
import unicodedata
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus

import pyautogui
import numpy as np

try:
    from yt_dlp import YoutubeDL
    _YTDLP_OK = True
except Exception:
    YoutubeDL = None
    _YTDLP_OK = False

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:
    _TRANSCRIPT_OK = False

from config import get_os, is_windows, is_mac, is_linux


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


from actions.paths import RESOURCE_DIR, config_path

BASE_DIR        = RESOURCE_DIR
API_CONFIG_PATH = config_path("api_keys.json")
DOWNLOAD_STATE_FILE = config_path("download_state.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_YT_VIDEO_FILTER = "EgIQAQ%3D%3D"
_YOUTUBE_STOPWORDS = {
    "a", "an", "and", "de", "del", "el", "en", "la", "las", "le", "los", "o",
    "por", "the", "to", "un", "una", "video", "youtube", "yt", "watch",
    "ultimo", "último", "latest", "recent", "reciente", "nuevo", "nueva",
    "mas", "más", "mejor",
}


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


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _open_url(url: str) -> None:
    try:
        if is_mac():
            subprocess.Popen(["open", url])
        elif is_linux():
            subprocess.Popen(["xdg-open", url])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception as e:
        print(f"[YouTube] ⚠️ open_url failed: {e}")

def _normalize_query_text(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _clean_video_query(query: str) -> str:
    raw = str(query or "").strip()
    cleaned = re.sub(
        r"\b(?:youtube|yt|video|vídeo|videos|vídeos|watch|ver)\b",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:ultimo|último|latest|recent|reciente|nuevo|nueva|más reciente|mas reciente)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or raw


def _parse_upload_timestamp(entry: dict) -> int:
    ts = entry.get("timestamp")
    if isinstance(ts, (int, float)):
        return int(ts)
    upload_date = str(entry.get("upload_date", "") or "").strip()
    if re.fullmatch(r"\d{8}", upload_date):
        try:
            return int(datetime.strptime(upload_date, "%Y%m%d").timestamp())
        except Exception:
            return 0
    return 0


def _search_youtube_candidates(query: str, limit: int = 12) -> list[dict]:
    if not _YTDLP_OK:
        return []

    search_query = f"ytsearch{limit}:{query}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
        entries = info.get("entries") if isinstance(info, dict) else []
        return [e for e in entries or [] if isinstance(e, dict)]
    except Exception as e:
        print(f"[YouTube] ⚠️ ytsearch failed: {e}")
        return []


def search_youtube_best_match(query: str) -> dict:
    candidates = _search_youtube_candidates(_clean_video_query(query), limit=12)
    if not candidates:
        return {}
    best = None
    best_score = float("-inf")
    for idx, entry in enumerate(candidates):
        score = _score_video_candidate(entry, query, prefer_latest=False) - (idx * 0.05)
        if score > best_score:
            best = entry
            best_score = score
    if not best:
        return {}
    vid = best.get("id") or best.get("video_id")
    return {
        "id": vid,
        "title": best.get("title", ""),
        "url": best.get("webpage_url") or best.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else ""),
        "uploader": best.get("uploader", "") or best.get("channel", ""),
        "duration": best.get("duration"),
    }


def retry_failed_downloads(output_dir: str = "", progress_hook=None, cancel_event=None) -> list[str]:
    failed = list(_DOWNLOAD_STATE.get("last_failed", []))
    if not failed:
        return []
    retried = []
    for item in failed:
        try:
            url = item.get("url") or ""
            quality = item.get("quality") or _DOWNLOAD_STATE.get("video_quality", "best")
            if url:
                retried.append(download_video(url, output_dir=output_dir, quality=quality, progress_hook=progress_hook, cancel_event=cancel_event))
        except Exception:
            continue
    _DOWNLOAD_STATE["last_failed"] = []
    _save_download_state(_DOWNLOAD_STATE)
    return retried


def _score_video_candidate(entry: dict, query: str, prefer_latest: bool = False) -> float:
    q_norm = _normalize_query_text(query)
    q_words = [
        word for word in q_norm.split()
        if len(word) > 2 and word not in _YOUTUBE_STOPWORDS
    ] or q_norm.split()

    title = _normalize_query_text(entry.get("title", ""))
    uploader = _normalize_query_text(entry.get("uploader", "") or entry.get("channel", ""))
    description = _normalize_query_text(entry.get("description", ""))

    score = 0.0
    for word in q_words:
        if word and word in title:
            score += 4.0
        if word and word in uploader:
            score += 1.5
        if word and word in description:
            score += 0.5

    if "podcast" in q_norm and "podcast" in title:
        score += 3.0

    if any(bad in title for bad in ("short", "clip", "trailer", "reaction", "teaser")):
        score -= 4.0

    duration = entry.get("duration")
    try:
        duration = int(duration or 0)
    except Exception:
        duration = 0

    if "podcast" in q_norm:
        if duration and duration < 600:
            score -= 8.0
        elif duration and duration >= 1800:
            score += 2.5

    if prefer_latest:
        score += min(6.0, _parse_upload_timestamp(entry) / 1_000_000_000)
    else:
        score += min(2.0, _parse_upload_timestamp(entry) / 2_000_000_000)

    if entry.get("live_status") == "is_live":
        score -= 3.0

    return score


def _scrape_first_video_url(query: str) -> str | None:
    cleaned = _clean_video_query(query)
    prefer_latest = any(
        token in _normalize_query_text(query)
        for token in ("ultimo", "latest", "recent", "reciente", "nueva", "nuevo")
    )

    candidates = _search_youtube_candidates(cleaned, limit=12)
    if not candidates:
        candidates = _search_youtube_candidates(cleaned, limit=12)

    best_url = None
    best_score = float("-inf")
    for idx, entry in enumerate(candidates):
        vid = entry.get("id") or entry.get("video_id")
        url = entry.get("webpage_url") or entry.get("url") or (
            f"https://www.youtube.com/watch?v={vid}" if vid else ""
        )
        if not url:
            continue
        score = _score_video_candidate(entry, cleaned, prefer_latest=prefer_latest) - (idx * 0.05)
        if score > best_score:
            best_score = score
            best_url = url

    if best_url:
        return best_url

    if not _REQUESTS_OK:
        return None

    search_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(cleaned)}"
        f"&sp={_YT_VIDEO_FILTER}"
    )

    try:
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        html = r.text

        video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)

        seen = set()
        for vid in video_ids:
            if vid in seen:
                continue
            seen.add(vid)

            if f'/shorts/{vid}' in html:
                continue
            return f"https://www.youtube.com/watch?v={vid}"

    except Exception as e:
        print(f"[YouTube] ⚠️ scrape_first_video_url failed: {e}")

    return None

def _extract_video_id(url: str) -> str | None:
    match = re.search(
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url
    )
    return match.group(1) if match else None


def _is_valid_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url or ""))


def _downloads_dir(subdir: str = "JARVIS_Videos") -> Path:
    out = Path.home() / "Downloads" / subdir
    out.mkdir(parents=True, exist_ok=True)
    return out


def set_default_quality(audio: str = "", video: str = "") -> dict:
    if audio:
        _DOWNLOAD_STATE["audio_quality"] = audio
    if video:
        _DOWNLOAD_STATE["video_quality"] = video
    _save_download_state(_DOWNLOAD_STATE)
    return {
        "audio_quality": _DOWNLOAD_STATE.get("audio_quality", "best"),
        "video_quality": _DOWNLOAD_STATE.get("video_quality", "best"),
    }


def download_status() -> dict:
    return {
        "audio_quality": _DOWNLOAD_STATE.get("audio_quality", "best"),
        "video_quality": _DOWNLOAD_STATE.get("video_quality", "best"),
        "last_failed": _DOWNLOAD_STATE.get("last_failed", []),
    }


def open_download_folder(kind: str = "video") -> str:
    folder = _downloads_dir("JARVIS_Videos" if str(kind).lower().startswith("v") else "JARVIS_Audio")
    try:
        subprocess.Popen(["explorer", str(folder)])
    except Exception:
        pass
    return str(folder)


def cleanup_partial_downloads(kind: str = "video") -> list[str]:
    base = _downloads_dir("JARVIS_Videos" if str(kind).lower().startswith("v") else "JARVIS_Audio")
    removed = []
    for path in base.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".part", ".ytdl", ".tmp"):
            try:
                path.unlink()
                removed.append(str(path))
            except Exception:
                pass
    return removed


def _normalize_quality(quality: str) -> str:
    q = _normalize_query_text(quality)
    if q in ("best", "max", "highest", "mejor", "alta", "high", ""):
        return "best"
    if q in ("baja", "low", "480", "480p", "sd"):
        return "low"
    if q in ("media", "medium", "720", "720p", "hd"):
        return "medium"
    if q in ("alta", "high", "1080", "1080p", "fullhd", "fhd"):
        return "high"
    return "best"


def _video_format_selector(quality: str) -> str:
    q = _normalize_quality(quality)
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    if ffmpeg_ok:
        if q == "low":
            return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        if q == "medium":
            return "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        if q == "high":
            return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        return "bestvideo+bestaudio/best"
    if q == "low":
        return "best[height<=480][acodec!=none]/best[acodec!=none]/best"
    if q == "medium":
        return "best[height<=720][acodec!=none]/best[acodec!=none]/best"
    if q == "high":
        return "best[height<=720][acodec!=none]/best[acodec!=none]/best"
    return "best[acodec!=none]/best"


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


def _resolve_video_url(query_or_url: str) -> str | None:
    raw = (query_or_url or "").strip()
    if not raw:
        return None
    if _is_valid_youtube_url(raw):
        return raw
    if len(raw) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return f"https://www.youtube.com/watch?v={raw}"
    return _scrape_first_video_url(raw)


def download_video(query_or_url: str, output_dir: str = "", quality: str = "", progress_hook=None, cancel_event=None) -> str:
    """Download a YouTube video and return the saved file path."""
    if not _YTDLP_OK:
        return "yt-dlp is not installed in this environment."

    if not str(quality or "").strip():
        return "Antes de descargar, dime qué calidad quieres: baja, media, alta o best."

    url = _resolve_video_url(query_or_url)
    if not url:
        return "No valid YouTube URL or search query was provided."

    outdir = Path(output_dir).expanduser() if output_dir else _downloads_dir()
    outdir.mkdir(parents=True, exist_ok=True)

    format_selector = _video_format_selector(quality)
    title_ref = _clean_video_query(query_or_url) or "video"

    _emit_download_state(progress_hook, active=True, percent=0, label="Preparing download", detail=title_ref, can_cancel=True)

    def _hook(data: dict):
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        status = data.get("status")
        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            percent = (downloaded / total * 100.0) if total > 0 else 0.0
            speed = data.get("speed")
            eta = data.get("eta")
            detail = title_ref
            extra = []
            if speed:
                try:
                    extra.append(f"{float(speed) / 1024 / 1024:.1f} MB/s")
                except Exception:
                    pass
            if eta is not None:
                extra.append(f"ETA {int(eta)}s")
            if extra:
                detail = f"{title_ref} · " + " · ".join(extra)
            _emit_download_state(progress_hook, active=True, percent=percent, label="Downloading video", detail=detail, can_cancel=True)
        elif status == "finished":
            _emit_download_state(progress_hook, active=True, percent=100, label="Finalizing video", detail=title_ref, can_cancel=True)

    opts = {
        "format": format_selector,
        "outtmpl": str(outdir / "%(title).120s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "progress_hooks": [_hook],
    }
    if shutil.which("ffmpeg") is not None and format_selector == "bestvideo+bestaudio/best":
        opts["merge_output_format"] = "mp4"

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            saved = str(Path(ydl.prepare_filename(info)))
            _emit_download_state(progress_hook, active=False, percent=100, label="Download complete", detail=saved, can_cancel=False)
            return saved
    except DownloadCancelled:
        _emit_download_state(progress_hook, active=False, percent=0, label="Download cancelled", detail=title_ref, can_cancel=False)
        return "Descarga cancelada."
    except Exception as e:
        _DOWNLOAD_STATE.setdefault("last_failed", []).append({
            "title": title_ref,
            "url": url,
            "quality": quality,
            "error": str(e)[:120],
        })
        _save_download_state(_DOWNLOAD_STATE)
        _emit_download_state(progress_hook, active=False, percent=0, label="Download failed", detail=title_ref, can_cancel=False)
        raise


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()

        url = simpledialog.askstring("J.A.R.V.I.S", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as e:
        print(f"[YouTube] ⚠️ URL dialog failed: {e}")
        return None


def _get_transcript(video_id: str) -> str | None:
    if not _TRANSCRIPT_OK:
        return None
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript      = None

        lang_priority = ["en", "tr", "de", "fr", "es", "it", "pt", "ru", "ja", "ko", "ar", "zh"]

        try:
            transcript = transcript_list.find_manually_created_transcript(lang_priority)
        except Exception:
            pass

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(lang_priority)
            except Exception:
                for t in transcript_list:
                    transcript = t
                    break

        if transcript is None:
            return None

        fetched = transcript.fetch()
        return " ".join(entry["text"] for entry in fetched)

    except Exception as e:
        print(f"[YouTube] ⚠️ Transcript fetch failed: {e}")
        return None


def _summarize_with_gemini(transcript: str, video_url: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are JARVIS, an AI assistant. "
            "Summarize YouTube video transcripts clearly and concisely. "
            "Structure: 1-sentence overview, then 3-5 key points. "
            "Be direct. Address the user as 'sir'. "
            "Match the language of the transcript."
        )
    )

    max_chars = 80000
    truncated = transcript[:max_chars] + ("..." if len(transcript) > max_chars else "")
    response  = model.generate_content(
        f"Please summarize this YouTube video transcript:\n\n{truncated}"
    )
    return response.text.strip()


def _save_summary(content: str, video_url: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"youtube_summary_{ts}.txt"
    desktop  = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / filename

    header = (
        f"JARVIS — YouTube Summary\n"
        f"{'─' * 50}\n"
        f"URL    : {video_url}\n"
        f"Date   : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'─' * 50}\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")

    try:
        if is_windows():
            subprocess.Popen(["notepad.exe", str(filepath)])
        elif is_mac():
            subprocess.Popen(["open", "-t", str(filepath)])
        else:
            subprocess.Popen(["xdg-open", str(filepath)])
    except Exception as e:
        print(f"[YouTube] ⚠️ Could not open text editor: {e}")

    return str(filepath)


def _scrape_video_info(video_id: str) -> dict:
    if not _REQUESTS_OK:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text
        info = {}

        for key, pattern in [
            ("title",    r'"title":\{"runs":\[\{"text":"([^"]+)"'),
            ("channel",  r'"ownerChannelName":"([^"]+)"'),
            ("views",    r'"viewCount":"(\d+)"'),
            ("duration", r'"lengthSeconds":"(\d+)"'),
            ("likes",    r'"label":"([0-9,]+ likes)"'),
        ]:
            match = re.search(pattern, html)
            if match:
                raw = match.group(1)
                if key == "views":
                    info[key] = f"{int(raw):,}"
                elif key == "duration":
                    secs = int(raw)
                    info[key] = f"{secs // 60}:{secs % 60:02d}"
                else:
                    info[key] = raw

        return info
    except Exception as e:
        print(f"[YouTube] ⚠️ Info scrape failed: {e}")
        return {}


def _scrape_trending(region: str = "TR", max_results: int = 8) -> list[dict]:
    if not _REQUESTS_OK:
        return []
    url = f"https://www.youtube.com/feed/trending?gl={region.upper()}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text

        titles   = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', html)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)

        results, seen = [], set()
        for i, title in enumerate(titles):
            if title in seen or len(title) < 5:
                continue
            seen.add(title)
            channel = channels[i] if i < len(channels) else "Unknown"
            results.append({"rank": len(results) + 1, "title": title, "channel": channel})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        print(f"[YouTube] ⚠️ Trending scrape failed: {e}")
        return []

def _handle_play(parameters: dict, player) -> str:
    query = parameters.get("query", "").strip()
    if not query:
        return "Please tell me what you'd like to watch, sir."

    if player:
        player.write_log(f"[YouTube] Searching: {query}")

    print(f"[YouTube] 🔍 Scraping first non-Shorts video for: {query}")

    video_url = _scrape_first_video_url(query)

    if video_url:
        print(f"[YouTube] ▶️ Opening: {video_url}")
        _open_url(video_url)
        return f"Playing: {query}"

    print(f"[YouTube] ⚠️ Scrape failed, opening filtered search page")
    fallback_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
        f"&sp={_YT_VIDEO_FILTER}"
    )
    _open_url(fallback_url)
    return f"Opened YouTube search for: {query} (manual selection required)"


def _handle_summarize(parameters: dict, player, speak) -> str:
    if not _TRANSCRIPT_OK:
        return "youtube-transcript-api is not installed. Run: pip install youtube-transcript-api"

    url = _ask_for_url("Please paste the YouTube video URL:")
    if not url:
        return "No URL provided, sir. Summary cancelled."
    if not _is_valid_youtube_url(url):
        return "That doesn't appear to be a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID from that URL, sir."

    if player:
        player.write_log(f"[YouTube] Summarizing: {url}")
    if speak:
        speak("Fetching the transcript now, sir. One moment.")

    transcript = _get_transcript(video_id)
    if not transcript:
        return "I couldn't retrieve a transcript for that video, sir."

    if speak:
        speak("Transcript retrieved. Generating summary now.")

    try:
        summary = _summarize_with_gemini(transcript, url)
    except Exception as e:
        return f"Summary generation failed, sir: {e}"

    if speak:
        speak(summary)

    if parameters.get("save", False):
        saved_path = _save_summary(summary, url)
        return f"Summary complete and saved to Desktop: {saved_path}"

    return summary


def _handle_get_info(parameters: dict, player, speak) -> str:
    url = parameters.get("url", "").strip()
    if not url:
        url = _ask_for_url("Please paste the YouTube video URL:")
    if not url or not _is_valid_youtube_url(url):
        return "Please provide a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID, sir."

    if player:
        player.write_log(f"[YouTube] Getting info: {url}")

    info = _scrape_video_info(video_id)
    if not info:
        return "Could not retrieve video information, sir."

    lines = [
        f"{key.capitalize()}: {info[key]}"
        for key in ("title", "channel", "views", "duration", "likes")
        if key in info
    ]
    result = "\n".join(lines)

    if speak:
        speak(f"Here's the video info, sir. {result.replace(chr(10), '. ')}")

    return result


def _handle_trending(parameters: dict, player, speak) -> str:
    region = parameters.get("region", "TR").upper()

    if player:
        player.write_log(f"[YouTube] Trending: {region}")

    trending = _scrape_trending(region=region, max_results=8)
    if not trending:
        return f"Could not fetch trending videos for region {region}, sir."

    lines  = [f"Top trending videos in {region}:"]
    lines += [f"{v['rank']}. {v['title']} — {v['channel']}" for v in trending]
    result = "\n".join(lines)

    if speak:
        top3   = trending[:3]
        spoken = "Here are the top trending videos, sir. " + ". ".join(
            f"Number {v['rank']}: {v['title']} by {v['channel']}" for v in top3
        )
        speak(spoken)

    return result


def _handle_download_video(parameters: dict, player, speak) -> str:
    query_or_url = (
        parameters.get("url")
        or parameters.get("query")
        or parameters.get("video_id")
        or ""
    )
    output_dir = parameters.get("output_dir") or parameters.get("path") or ""
    quality = parameters.get("quality") or ""
    if not str(quality).strip():
        return "Antes de descargar el video, dime qué calidad quieres: baja, media, alta o best."
    if player:
        player.write_log(f"[YouTube] Downloading video: {query_or_url}")
    result = download_video(query_or_url, output_dir=output_dir, quality=quality)
    if speak and isinstance(result, str) and not result.lower().startswith(("yt-dlp is not", "no valid")):
        speak("Video downloaded, sir.")
    return result


def _handle_download_status(parameters: dict, player, speak) -> dict:
    return download_status()


def _handle_open_download_folder(parameters: dict, player, speak) -> str:
    kind = parameters.get("kind", "video")
    return open_download_folder(kind)


def _handle_cleanup_partial_downloads(parameters: dict, player, speak) -> list[str]:
    kind = parameters.get("kind", "video")
    return cleanup_partial_downloads(kind)


def _handle_retry_failed_downloads(parameters: dict, player, speak) -> list[str]:
    output_dir = parameters.get("output_dir") or parameters.get("path") or ""
    return retry_failed_downloads(output_dir=output_dir)


def _handle_set_default_quality(parameters: dict, player, speak) -> dict:
    return set_default_quality(
        audio=parameters.get("audio_quality") or parameters.get("quality") or "",
        video=parameters.get("video_quality") or "",
    )


def _handle_search_youtube_best_match(parameters: dict, player, speak) -> dict:
    query = parameters.get("query") or ""
    return search_youtube_best_match(query)

_ACTION_MAP = {
    "play":      _handle_play,
    "summarize": _handle_summarize,
    "get_info":  _handle_get_info,
    "trending":  _handle_trending,
    "download_video": _handle_download_video,
    "download_status": _handle_download_status,
    "open_download_folder": _handle_open_download_folder,
    "cleanup_partial_downloads": _handle_cleanup_partial_downloads,
    "retry_failed_downloads": _handle_retry_failed_downloads,
    "set_default_quality": _handle_set_default_quality,
    "search_youtube_best_match": _handle_search_youtube_best_match,
}


def youtube_video(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "play").lower().strip()

    if player:
        player.write_log(f"[YouTube] Action: {action}")
    print(f"[YouTube] ▶️  Action: {action}  Params: {params}")

    handler = _ACTION_MAP.get(action)
    if handler is None:
        return (
            f"Unknown YouTube action: '{action}'. "
            "Available: play, summarize, get_info, trending, download_video, download_status, open_download_folder, cleanup_partial_downloads, retry_failed_downloads, set_default_quality, search_youtube_best_match."
        )

    try:
        if action == "play":
            return handler(params, player) or "Done."
        return handler(params, player, speak) or "Done."
    except Exception as e:
        print(f"[YouTube] ❌ Error in {action}: {e}")
        return f"YouTube {action} failed, sir: {e}"
