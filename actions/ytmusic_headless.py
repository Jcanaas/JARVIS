"""Headless YouTube Music player using mpv + yt-dlp.

This module provides a minimal controller interface Jarvis can call to play/pause/seek
without opening a GUI. mpv must be installed on the system and `yt-dlp` available
on PATH (we installed the package; PATH may need update).

API:
  play(query)
  pause()
  resume()
  toggle_play()
  stop()
  volume(level)
  seek(seconds)
  current() -> dict (title, artists, position, duration, playing)

This implementation uses mpv JSON IPC via named pipe on Windows: \\.\\pipe\\jarvis_mpv
It attempts to start mpv if not found. If mpv is not installed, functions will return
informative errors and Jarvis can fallback to the GUI-based integration.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_PIPE_PATH = r"\\.\pipe\jarvis_mpv"
_MPV_EXE = "mpv"

# Simple playlist: stores list of {videoId, title, artists} and current index
_playlist: list = []
_playlist_idx: int = 0
_autoplay_enabled: bool = True
_autoplay_thread: Optional[threading.Thread] = None
_autoplay_last_switch: float = 0.0
_job_handle = None

# Crossfade: fade-out the last N seconds of a track, fade-in the next one.
# 0 = disabled.
_crossfade_secs: int = 3
_crossfade_enabled: bool = False   # off by default; toggled via set_crossfade()
_user_volume: int = 100            # user's intended volume (0-100)
_crossfade_fading_out: bool = False
import atexit as _atexit

def _cleanup_on_exit():
    global _proc, _shutting_down, _job_handle
    _shutting_down = True
    try:
        if _proc is not None and _proc.poll() is None:
            _send_command(["quit"])
            try:
                _proc.wait(timeout=2)
            except Exception:
                pass
            if _proc.poll() is None:
                _proc.terminate()
            _proc = None
    except Exception:
        pass
    try:
        if _job_handle is not None:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_job_handle)
            _job_handle = None
    except Exception:
        pass

_atexit.register(_cleanup_on_exit)
# Try to locate mpv.exe in the workspace root or tools folder, prefer that over PATH
def _locate_mpv() -> str:
    # workspace/resource root (handles PyInstaller frozen builds too)
    from actions.paths import RESOURCE_DIR
    root = RESOURCE_DIR
    candidates = [
        root / 'mpv.exe',
        root / 'tools' / 'mpv' / 'mpv.exe',
        root / 'tools' / 'mpv.exe',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # fallback to PATH
    import shutil
    exe = shutil.which('mpv') or shutil.which('mpv.exe')
    return exe or 'mpv'

# initialize _MPV_EXE to located path
_MPV_EXE = _locate_mpv()

_last_meta = {
    "title": "",
    "artists": "",
    "videoId": "",
    "duration": 0,
    "position": 0,
    "playing": False,
    "_sampled_at": 0.0,
}
_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()
_shutting_down = False
_STREAM_TTL_SECONDS = 60 * 60 * 2
_stream_cache: dict[str, dict] = {}
_stream_loading: set[str] = set()
_stream_lock = threading.Lock()


def _mpv_available() -> bool:
    try:
        subprocess.run([_MPV_EXE, "--version"], capture_output=True, timeout=3)
        return True
    except Exception:
        return False


def _disable_win_audio_ducking():
    """Disable Windows audio ducking (the feature that lowers music volume when
    a communication app like a microphone is active). Stored in registry under
    HKCU\\SOFTWARE\\Microsoft\\Multimedia\\Audio\\UserDuckingPreference:
      0=mute others, 1=reduce 80% (default), 2=reduce 50%, 3=do nothing."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Multimedia\Audio",
            0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY,
        )
        winreg.SetValueEx(key, "UserDuckingPreference", 0, winreg.REG_DWORD, 3)
        winreg.CloseKey(key)
    except Exception:
        pass


def _create_windows_job_for_child(proc: subprocess.Popen) -> bool:
    """Attach mpv to a Windows Job Object so it is killed when Jarvis exits.

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE makes the OS terminate all processes in
    the job when the last handle to the job object is closed — which happens
    automatically when the parent process exits, even if killed forcefully.
    """
    global _job_handle
    if os.name != "nt":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = wt.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        kernel32.SetInformationJobObject.argtypes = [
            wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD
        ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit",     ctypes.c_longlong),
                ("LimitFlags",             wt.DWORD),
                ("MinimumWorkingSetSize",  ctypes.c_size_t),
                ("MaximumWorkingSetSize",  ctypes.c_size_t),
                ("ActiveProcessLimit",     wt.DWORD),
                ("Affinity",              ctypes.c_size_t),
                ("PriorityClass",         wt.DWORD),
                ("SchedulingClass",       wt.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount",  ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount",   ctypes.c_ulonglong),
                ("WriteTransferCount",  ctypes.c_ulonglong),
                ("OtherTransferCount",  ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo",               IO_COUNTERS),
                ("ProcessMemoryLimit",   ctypes.c_size_t),
                ("JobMemoryLimit",       ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed",    ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation   = 9

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            return False

        proc_handle = wt.HANDLE(int(proc._handle))
        ok = kernel32.AssignProcessToJobObject(job, proc_handle)
        if not ok:
            kernel32.CloseHandle(job)
            return False

        _job_handle = job
        return True
    except Exception:
        return False


def _locate_ytdlp() -> Optional[str]:
    import shutil

    candidates = [
        shutil.which("yt-dlp"),
        shutil.which("yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp.exe"),
        str(Path(sys.executable).parent / "yt-dlp"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _wait_for_pipe(timeout_ms: int = 5000) -> bool:
    """Wait until mpv's named pipe is ready using WaitNamedPipe (Windows API)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            ok = kernel32.WaitNamedPipeW(_PIPE_PATH, 500)
            if ok:
                return True
            err = kernel32.GetLastError()
            if err == 2:  # ERROR_FILE_NOT_FOUND: pipe server not up yet
                time.sleep(0.1)
                continue
            if err == 0 or err == 231:  # 231 = ERROR_PIPE_BUSY (pipe exists but busy)
                time.sleep(0.05)
                continue
            # Any other error: pipe might be ready, try anyway
            return True
        return False
    except Exception:
        # ctypes unavailable: fallback to time-based wait
        time.sleep(1.5)
        return True


def _start_mpv():
    global _proc
    if _shutting_down:
        return False
    if _proc is not None and _proc.poll() is None:
        return True
    if not _mpv_available():
        return False
    # Add venv Scripts dir to PATH so mpv can find yt-dlp
    env = os.environ.copy()
    venv_scripts = str(Path(sys.executable).parent)
    env['PATH'] = venv_scripts + os.pathsep + env.get('PATH', '')
    # Disable Windows audio ducking so music isn't lowered while mic is active
    _disable_win_audio_ducking()
    args = [
        _MPV_EXE,
        "--no-video",
        "--idle=yes",
        f"--input-ipc-server={_PIPE_PATH}",
        "--force-window=no",
        "--ytdl-format=bestaudio/best",
        "--cache=yes",
    ]
    ytdlp_path = _locate_ytdlp()
    if ytdlp_path:
        args.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp_path}")
    try:
        _proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        _create_windows_job_for_child(_proc)
        # Use WaitNamedPipe to properly detect when mpv IPC is ready
        ready = _wait_for_pipe(timeout_ms=6000)
        return ready
    except Exception:
        _proc = None
        return False


def _parse_mpv_response(raw: bytes) -> Optional[dict]:
    """Parse mpv response bytes: finds the JSON line that is a command reply (has 'error' key)."""
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "error" in obj:
                return obj
        except Exception:
            pass
    return None


def _ipc_request(cmd: list, request_id: int = 1) -> Optional[dict]:
    """Send a JSON IPC command to mpv and return the response dict, or None on failure.
    Works with pywin32 (win32file) OR via ctypes (no extra deps)."""
    payload = (json.dumps({"command": cmd, "request_id": request_id}) + "\n").encode("utf-8")

    # Method 1: win32file / pywin32
    try:
        import win32file  # type: ignore
        handle = win32file.CreateFile(
            _PIPE_PATH,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None
        )
        try:
            win32file.WriteFile(handle, payload)
            _, data = win32file.ReadFile(handle, 65536)
        finally:
            win32file.CloseHandle(handle)
        return _parse_mpv_response(data)
    except ImportError:
        pass  # fall through to ctypes
    except Exception:
        return None

    # Method 2: ctypes (works without pywin32)
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        GENERIC_READ  = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        k32.CreateFileW.restype = ctypes.c_void_p
        h = k32.CreateFileW(_PIPE_PATH, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if h is None or ctypes.c_void_p(h).value in (None, -1):
            return None
        try:
            bw = wt.DWORD(0)
            if not k32.WriteFile(h, payload, len(payload), ctypes.byref(bw), None):
                return None
            buf = ctypes.create_string_buffer(65536)
            br  = wt.DWORD(0)
            if not k32.ReadFile(h, buf, 65536, ctypes.byref(br), None):
                return None
            return _parse_mpv_response(buf.raw[: br.value])
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _send_command(cmd: list) -> bool:
    """Send a fire-and-forget IPC command. Retries up to 5 times on failure."""
    payload = json.dumps({"command": cmd}) + "\n"
    for attempt in range(5):
        try:
            with open(_PIPE_PATH, "w+b", buffering=0) as p:
                p.write(payload.encode("utf-8"))
            return True
        except Exception:
            if attempt < 4:
                time.sleep(0.3)
    return False


def _get_mpv_property(prop: str):
    """Read a property from mpv via IPC. Returns value or None."""
    resp = _ipc_request(["get_property", prop])
    if resp and resp.get("error") == "success":
        return resp.get("data")
    return None


def _ytdlp_cmd(args: list) -> Optional[str]:
    """Run yt-dlp with given args; returns stdout on success, None on failure."""
    import shutil
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    bases = ([exe] if exe else []) + [[sys.executable, "-m", "yt_dlp"]]
    for base in bases:
        cmd = (base if isinstance(base, list) else [base]) + args
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return None


def _get_stream_url_and_duration(url: str) -> tuple[Optional[str], int]:
    """Returns (stream_url, duration_seconds). duration may be 0 on failure."""
    # Force best audio-only stream for consistent quality
    out = _ytdlp_cmd(["--format", "bestaudio[ext=m4a]/bestaudio/best",
                      "--print", "%(url)s\t%(duration)s", "--no-playlist", url])
    if out:
        parts = out.splitlines()[0].split("\t")
        if len(parts) == 2:
            stream, dur_s = parts[0].strip(), parts[1].strip()
            try:
                return stream, int(float(dur_s))
            except Exception:
                return stream, 0
    # Fallback: just get URL with -g
    out2 = _ytdlp_cmd(["--format", "bestaudio/best", "-g", "--no-playlist", url])
    if out2:
        return out2.splitlines()[0].strip(), 0
    return None, 0


# Keep backward-compatible alias
def _get_stream_url(url: str) -> Optional[str]:
    s, _ = _get_stream_url_and_duration(url)
    return s


def _video_page_url(vid: str) -> str:
    return f"https://music.youtube.com/watch?v={vid}"


def _cached_stream(vid: str) -> tuple[Optional[str], int]:
    now = time.time()
    with _stream_lock:
        item = _stream_cache.get(str(vid or ""))
        if not item:
            return None, 0
        if now - float(item.get("ts", 0) or 0) > _STREAM_TTL_SECONDS:
            _stream_cache.pop(str(vid or ""), None)
            return None, 0
        return item.get("url"), int(item.get("duration", 0) or 0)


def _wait_cached_stream(vid: str, timeout: float = 2.0) -> tuple[Optional[str], int]:
    deadline = time.monotonic() + max(0.0, float(timeout or 0))
    while time.monotonic() < deadline:
        cached_url, cached_dur = _cached_stream(vid)
        if cached_url:
            return cached_url, cached_dur
        with _stream_lock:
            loading = vid in _stream_loading
        if not loading:
            return None, 0
        time.sleep(0.05)
    return _cached_stream(vid)


def _resolve_stream_for_video(vid: str) -> tuple[Optional[str], int]:
    vid = str(vid or "").strip()
    if not vid:
        return None, 0
    cached_url, cached_dur = _cached_stream(vid)
    if cached_url:
        return cached_url, cached_dur
    stream, duration = _get_stream_url_and_duration(_video_page_url(vid))
    if stream:
        with _stream_lock:
            _stream_cache[vid] = {"url": stream, "duration": int(duration or 0), "ts": time.time()}
    return stream, int(duration or 0)


def _prefetch_video(vid: str):
    vid = str(vid or "").strip()
    if not vid:
        return
    cached_url, _ = _cached_stream(vid)
    if cached_url:
        return
    with _stream_lock:
        if vid in _stream_loading:
            return
        _stream_loading.add(vid)

    def worker():
        try:
            _resolve_stream_for_video(vid)
        finally:
            with _stream_lock:
                _stream_loading.discard(vid)

    threading.Thread(target=worker, daemon=True).start()


def warmup() -> bool:
    return _start_mpv()


def prefetch_tracks(tracks, start_index: int = 0, count: int = 4) -> dict:
    try:
        start = max(0, int(start_index or 0))
    except Exception:
        start = 0
    try:
        n = max(1, min(8, int(count or 4)))
    except Exception:
        n = 4
    items = list(tracks or [])
    _start_mpv()
    scheduled = 0
    for item in items[start:start + n]:
        vid = item.get("videoId") or item.get("video_id") if isinstance(item, dict) else ""
        if vid:
            _prefetch_video(str(vid))
            scheduled += 1
    return {"scheduled": scheduled}


def _prefetch_next_tracks(count: int = 3):
    if not _playlist:
        return
    items = []
    for offset in range(1, max(1, count) + 1):
        idx = (_playlist_idx + offset) % len(_playlist)
        items.append(_playlist[idx])
    prefetch_tracks(items, 0, len(items))


def _play_video(vid: str, title: str, artists: str) -> str:
    """Internal: start mpv playback using YouTube Music URL directly.
    mpv resolves the stream via its yt-dlp hook, ensuring proper quality."""
    if _shutting_down:
        return "Aplicación cerrándose."
    vid = str(vid or "").strip()
    stream_url, stream_duration = _cached_stream(vid)
    if not stream_url:
        stream_url, stream_duration = _wait_cached_stream(vid, timeout=2.0)
    if not stream_url:
        stream_url, stream_duration = _resolve_stream_for_video(vid)
    if not stream_url:
        with _lock:
            _last_meta.update({
                "title": "",
                "artists": "",
                "videoId": "",
                "duration": 0,
                "position": 0,
                "playing": False,
                "_sampled_at": 0.0,
            })
        return "No se pudo resolver el stream de YouTube Music."
    url = stream_url
    if not _start_mpv():
        return "mpv no pudo arrancarse."
    if _shutting_down:
        return "Aplicación cerrándose."
    ok = _send_command(["loadfile", url, "replace"])
    if ok:
        with _lock:
            _last_meta.update({
                "title": title,
                "artists": artists,
                "videoId": vid,
                "duration": float(stream_duration or 0),
                "position": 0,
                "playing": True,
                "_sampled_at": time.monotonic(),
            })
        _ensure_autoplay_worker()
        _prefetch_next_tracks()
        return f"Reproduciendo '{title}' — {artists}."
    return "No se pudo cargar la canción en mpv."


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on", "si", "sí")


def _artists_text(artists) -> str:
    if not artists:
        return ""
    if isinstance(artists, str):
        return artists
    if isinstance(artists, list):
        names = []
        for a in artists:
            if isinstance(a, dict):
                nm = a.get("name") or a.get("artist") or ""
                if nm:
                    names.append(str(nm))
            elif a:
                names.append(str(a))
        return ", ".join(names)
    return str(artists)


def _crossfade_fade_out_step(pos: float, dur: float) -> None:
    """Called from the autoplay worker to smoothly reduce volume near track end."""
    global _crossfade_fading_out
    if dur <= 0:
        return
    remaining = dur - pos
    cf_dur = float(_crossfade_secs)
    if remaining > cf_dur:
        # Not in crossfade zone yet — restore volume if we were previously fading
        if _crossfade_fading_out:
            _crossfade_fading_out = False
            _send_command(["set_property", "volume", _user_volume])
        return
    # Fade progress: 1.0 at start of fade, 0.0 at track end
    progress = max(0.0, remaining / cf_dur)
    target_vol = int(_user_volume * progress)
    _crossfade_fading_out = True
    _send_command(["set_property", "volume", max(0, target_vol)])


def _crossfade_fade_in(duration_secs: float = None) -> None:
    """Start a fade-in on the current track from 0 to _user_volume over crossfade duration."""
    if duration_secs is None:
        duration_secs = float(_crossfade_secs)

    def _fade():
        steps = max(1, int(duration_secs / 0.08))
        for i in range(steps + 1):
            if _shutting_down:
                break
            vol = int(_user_volume * i / steps)
            _send_command(["set_property", "volume", vol])
            time.sleep(0.08)
        _send_command(["set_property", "volume", _user_volume])

    threading.Thread(target=_fade, daemon=True).start()


def _ensure_autoplay_worker() -> None:
    """Start the combined position-poller + autoplay thread if not running."""
    global _autoplay_thread
    if _autoplay_thread is not None and _autoplay_thread.is_alive():
        return

    def _worker():
        global _autoplay_last_switch, _crossfade_fading_out
        _poll_pos  = 0.0   # last non-None time-pos from mpv
        _poll_dur  = 0.0   # last non-None duration from mpv
        _eof_seen  = False  # did we already trigger advance on this eof?

        while not _shutting_down:
            try:
                if _proc is not None and _proc.poll() is None:
                    # --- Poll live position/state from mpv IPC ---
                    pos    = _get_mpv_property("time-pos")
                    dur    = _get_mpv_property("duration")
                    paused = _get_mpv_property("pause")
                    eof    = _get_mpv_property("eof-reached")
                    idle   = _get_mpv_property("idle-active")

                    with _lock:
                        if pos is not None:
                            _last_meta["position"] = float(pos)
                            _last_meta["_sampled_at"] = time.monotonic()
                            _poll_pos = float(pos)
                            _eof_seen = False  # still playing, reset guard
                        if dur is not None:
                            _last_meta["duration"] = float(dur)
                            _poll_dur = float(dur)
                        if paused is not None:
                            _last_meta["playing"] = not bool(paused)
                        if idle is not None and bool(idle):
                            _last_meta["playing"] = False
                        if eof and not (_autoplay_enabled and _playlist):
                            _last_meta["playing"] = False

                    # --- Crossfade fade-out when approaching track end ---
                    is_paused = bool(_get_mpv_property("pause"))
                    if _crossfade_enabled and not is_paused and _poll_dur > 0:
                        _crossfade_fade_out_step(_poll_pos, _poll_dur)

                    # --- Autoplay: advance on eof-reached OR near end ---
                    if _autoplay_enabled and _playlist:
                        cf_margin = float(_crossfade_secs) if _crossfade_enabled else 1.5
                        eof_hit  = bool(eof)
                        near_end = _poll_dur > 0 and _poll_pos >= _poll_dur - cf_margin
                        if (eof_hit or near_end) and not _eof_seen:
                            now = time.time()
                            if now - _autoplay_last_switch > 3.0:
                                _eof_seen = True
                                _autoplay_last_switch = now
                                _crossfade_fading_out = False
                                next()
                else:
                    with _lock:
                        _last_meta["playing"] = False
            except Exception:
                pass
            time.sleep(0.8)

    _autoplay_thread = threading.Thread(target=_worker, daemon=True)
    _autoplay_thread.start()


def set_autoplay(enabled: bool = True) -> str:
    global _autoplay_enabled
    _autoplay_enabled = _to_bool(enabled)
    if _autoplay_enabled:
        _ensure_autoplay_worker()
    return f"Autoplay {'activado' if _autoplay_enabled else 'desactivado'}."


def show_queue(limit: int = 20) -> str:
    if not _playlist:
        return "La cola está vacía."
    try:
        lim = max(1, min(100, int(limit)))
    except Exception:
        lim = 20
    lines = [f"Cola ({len(_playlist)} canciones):"]
    end = min(len(_playlist), lim)
    for i in range(end):
        t = _playlist[i]
        mark = "▶" if i == _playlist_idx else " "
        lines.append(f"{mark} {i+1}. {t.get('title','')} — {t.get('artists','')}")
    if len(_playlist) > lim:
        lines.append(f"... y {len(_playlist)-lim} más")
    return "\n".join(lines)


def list_playlists(limit: int | None = None) -> str:
    try:
        from actions.ytmusic import _get_ytmusic
        yt = _get_ytmusic(require_auth=True)
        try:
            resolved_limit = None if limit is None or int(limit) <= 0 else int(limit)
        except (TypeError, ValueError):
            resolved_limit = None
        pls = yt.get_library_playlists(limit=resolved_limit)
    except Exception as e:
        return f"No se pudieron leer tus listas: {e}"

    if not pls:
        return "No se encontraron listas en tu biblioteca."
    lines = [f"Tus listas ({len(pls)}):"]
    for p in pls:
        title = p.get("title", "")
        author = p.get("author", "")
        pid = p.get("playlistId") or p.get("browseId") or ""
        lines.append(f"- {title} — {author} [{pid}]")
    return "\n".join(lines)


def _build_playlist_from_tracks(tracks, shuffle: bool = False) -> list:
    out = []
    for t in tracks or []:
        vid = t.get("videoId")
        if not vid:
            continue
        out.append({
            "videoId": vid,
            "title": t.get("title", ""),
            "artists": _artists_text(t.get("artists")),
        })
    if shuffle and out:
        random.shuffle(out)
    return out


def _load_and_play_playlist(items: list, start_idx: int = 0) -> str:
    global _playlist, _playlist_idx
    if not items:
        return "La lista está vacía."
    _playlist = items
    _playlist_idx = max(0, min(int(start_idx), len(_playlist) - 1))
    cur = _playlist[_playlist_idx]
    return _play_video(cur["videoId"], cur["title"], cur["artists"])


def play_track(video_id: str = "", title: str = "", artists: str = "") -> str:
    vid = str(video_id or "").strip()
    if not vid:
        return "No hay videoId para reproducir."
    return _load_and_play_playlist([{
        "videoId": vid,
        "title": str(title or ""),
        "artists": str(artists or ""),
    }], 0)


def play_tracks(tracks, start_index: int = 0, shuffle: bool = False) -> str:
    items = _build_playlist_from_tracks(tracks or [], shuffle=shuffle)
    if not items:
        return "La lista está vacía."
    return _load_and_play_playlist(items, start_index)


def play_liked(limit: int | None = None, shuffle: bool = False) -> str:
    try:
        from actions.ytmusic import get_liked_songs
        songs = get_liked_songs(limit=limit)
    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"No se pudieron cargar tus Me gusta: {e}"

    items = _build_playlist_from_tracks(songs, shuffle=shuffle)
    if not items:
        return "No tienes canciones en Me gusta."
    return _load_and_play_playlist(items, 0)


def play_playlist(query_or_id: str = "", limit: int | None = None, shuffle: bool = False, start_index: int = 0) -> str:
    try:
        from actions.ytmusic import list_playlist_tracks
        tracks = list_playlist_tracks(
            query_or_id=query_or_id,
            limit=limit,
            shuffle=False,
        )
        if not tracks:
            return "No encontré esa lista. Usa list_playlists para ver tus listas."

        items = _build_playlist_from_tracks(tracks, shuffle=shuffle)
        if not items:
            return "Esa lista no tiene pistas reproducibles."
        return _load_and_play_playlist(items, start_index)
    except Exception as e:
        return f"No se pudo reproducir la lista: {e}"


def play(query: str) -> str:
    """Search using actions.ytmusic.search_songs and play first match headless.
    Loads up to 10 results into the playlist for next()/previous() navigation."""
    global _playlist, _playlist_idx
    try:
        from actions.ytmusic import search_songs
    except Exception:
        return "No se puede buscar; módulo `actions.ytmusic` no disponible."

    results = search_songs(query, limit=10)
    if not results:
        return f"No se encontró '{query}'."

    # Build playlist
    new_pl = []
    for r in results:
        vid = r.get("videoId")
        if vid:
            new_pl.append({
                "videoId": vid,
                "title":   r.get("title", ""),
                "artists": r.get("artists", ""),
            })
    if not new_pl:
        return f"No se pudo obtener URL para '{query}'."

    return _load_and_play_playlist(new_pl, 0)


def next() -> str:
    """Skip to next track in playlist."""
    global _playlist_idx, _crossfade_fading_out
    if not _playlist:
        return "No hay lista de reproducción."
    _playlist_idx = (_playlist_idx + 1) % len(_playlist)
    t = _playlist[_playlist_idx]
    # Reset volume before loading next track, then fade in if crossfade is on
    _crossfade_fading_out = False
    if _crossfade_enabled:
        _send_command(["set_property", "volume", 0])
    else:
        _send_command(["set_property", "volume", _user_volume])
    result = _play_video(t["videoId"], t["title"], t["artists"])
    if _crossfade_enabled:
        _crossfade_fade_in()
    return result


def previous() -> str:
    """Skip to previous track in playlist."""
    global _playlist_idx, _crossfade_fading_out
    if not _playlist:
        return "No hay lista de reproducción."
    _playlist_idx = (_playlist_idx - 1) % len(_playlist)
    t = _playlist[_playlist_idx]
    _crossfade_fading_out = False
    if _crossfade_enabled:
        _send_command(["set_property", "volume", 0])
    else:
        _send_command(["set_property", "volume", _user_volume])
    result = _play_video(t["videoId"], t["title"], t["artists"])
    if _crossfade_enabled:
        _crossfade_fade_in()
    return result


def pause() -> bool:
    ok = _send_command(["set_property", "pause", True])
    if ok:
        with _lock:
            _last_meta["playing"] = False
            _last_meta["_sampled_at"] = time.monotonic()
    return ok


def resume() -> bool:
    ok = _send_command(["set_property", "pause", False])
    if ok:
        with _lock:
            _last_meta["playing"] = True
            _last_meta["_sampled_at"] = time.monotonic()
    return ok


def toggle_play() -> bool:
    return _send_command(["cycle", "pause"]) 


def stop() -> bool:
    ok = _send_command(["stop"])
    if ok:
        with _lock:
            _last_meta.update({
                "title": "",
                "artists": "",
                "videoId": "",
                "duration": 0,
                "position": 0,
                "playing": False,
                "_sampled_at": 0.0,
            })
    return ok


def volume(level: int) -> bool:
    global _user_volume
    try:
        lvl = max(0, min(100, int(level)))
    except Exception:
        lvl = 50
    _user_volume = lvl
    ok = _send_command(["set_property", "volume", lvl])
    return ok


def set_crossfade(seconds: int = 3, enabled: bool = True) -> str:
    """Enable or disable crossfade and set its duration in seconds (1-15)."""
    global _crossfade_secs, _crossfade_enabled
    try:
        secs = max(1, min(15, int(seconds)))
    except Exception:
        secs = 3
    _crossfade_secs = secs
    _crossfade_enabled = bool(enabled)
    state = "activado" if _crossfade_enabled else "desactivado"
    return f"Crossfade {state} ({_crossfade_secs}s)."


def get_crossfade() -> dict:
    """Return current crossfade settings."""
    return {"enabled": _crossfade_enabled, "seconds": _crossfade_secs}


def seek(seconds: int) -> bool:
    try:
        s = int(seconds)
    except Exception:
        return False
    ok = _send_command(["seek", s, "absolute"])
    if ok:
        with _lock:
            _last_meta["position"] = float(s)
            _last_meta["_sampled_at"] = time.monotonic()
    return ok


def play_from_file(file_path: str, shuffle: bool = False) -> str:
    """Load a Jarvis playlist JSON exported with export_liked_to_file /
    export_playlist_to_file and start playback."""
    try:
        from actions.ytmusic import import_playlist_from_file
        tracks = import_playlist_from_file(file_path)
    except Exception as e:
        return f"No se pudo leer la playlist: {e}"
    if not tracks:
        return "La playlist importada está vacía o no tiene videoIds."
    return play_tracks(tracks, start_index=0, shuffle=shuffle)


def current() -> dict:
    with _lock:
        base = dict(_last_meta)
    if _proc is None or _proc.poll() is not None:
        base["playing"] = False
        base.pop("_sampled_at", None)
        return base
    # If the poller thread is alive it already keeps _last_meta fresh every ~0.8s
    if _autoplay_thread is not None and _autoplay_thread.is_alive():
        sampled_at = float(base.pop("_sampled_at", 0.0) or 0.0)
        if base.get("playing") and sampled_at > 0:
            elapsed = max(0.0, time.monotonic() - sampled_at)
            duration = float(base.get("duration") or 0.0)
            position = float(base.get("position") or 0.0) + elapsed
            base["position"] = min(duration, position) if duration > 0 else position
        return base
    # Poller not running yet: do a one-off live IPC query
    if _proc is not None and _proc.poll() is None:
        pos    = _get_mpv_property("time-pos")
        paused = _get_mpv_property("pause")
        dur    = _get_mpv_property("duration")
        with _lock:
            if pos is not None:
                _last_meta["position"] = float(pos)
                _last_meta["_sampled_at"] = time.monotonic()
                base["position"] = _last_meta["position"]
            if paused is not None:
                _last_meta["playing"] = not bool(paused)
                base["playing"] = _last_meta["playing"]
            if dur is not None:
                _last_meta["duration"] = float(dur)
                base["duration"] = _last_meta["duration"]
    base.pop("_sampled_at", None)
    return base
