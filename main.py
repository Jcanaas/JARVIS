import asyncio
import re
import threading
import json
import os
import warnings
import sys
import traceback
import time
from pathlib import Path
from typing import Optional


def _install_safe_std_streams() -> None:
    """Make stdout/stderr robust for a frozen windowed build.

    When launched without a console (PyInstaller windowed) sys.stdout/stderr are
    None, and when output is redirected to a cp1252 file the emoji in our log
    lines raise UnicodeEncodeError — which previously killed worker threads.
    Force UTF-8 with error replacement, or a null sink when no stream exists.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
            except Exception:
                pass
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_install_safe_std_streams()


def _silence_child_consoles() -> None:
    """Stop child processes from flashing console windows.

    In the windowed (no-console) frozen build, every subprocess that is itself a
    console program (nvidia-smi, powershell, mpv, node, yt-dlp, …) pops a black
    terminal for a split second. Several of these run on UI timers, so the user
    sees terminals appearing every few seconds. Patch subprocess.Popen once to
    add CREATE_NO_WINDOW on Windows — this covers run/call/check_output too,
    since they all funnel through Popen.
    """
    if os.name != "nt":
        return
    import subprocess
    if getattr(subprocess.Popen, "_jarvis_no_window", False):
        return
    CREATE_NO_WINDOW = 0x08000000
    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):
        try:
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
        except Exception:
            pass
        _orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init
    subprocess.Popen._jarvis_no_window = True


_silence_child_consoles()

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
from memory.conversation_history import (
    save_turn as _save_turn,
    format_for_prompt as _fmt_history,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.capabilities      import capabilities_catalog
from actions.personal_tools    import personal_tools
from actions.system_tools      import system_tools
from actions.productivity_tools import productivity_tools
from actions.utility_tools     import utility_tools
from actions.game_updater      import game_updater
from actions.google_calendar   import google_calendar
from actions.gmail             import gmail
from actions.gdrive            import gdrive
from actions.ytmusic           import ytmusic


from actions.paths import RESOURCE_DIR, config_path


def get_base_dir():
    """Read-only resource root (writable data lives under actions.paths.DATA_DIR)."""
    return RESOURCE_DIR


BASE_DIR        = RESOURCE_DIR
API_CONFIG_PATH = config_path("api_keys.json")
PROMPT_PATH     = RESOURCE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024


def _compute_bands(samples_int16_flat, samplerate):
    """Calcula (bass, mid, treble) normalizados 0-1 a partir de muestras int16."""
    n = len(samples_int16_flat)
    if n < 64:
        return 0.0, 0.0, 0.0
    f32 = samples_int16_flat.astype('float32')
    rms = float(np.sqrt(np.mean(f32 ** 2))) / 32768.0
    if rms < 2e-4:
        return 0.0, 0.0, 0.0
    nfft  = min(n, 1024)
    mag2  = np.abs(np.fft.rfft(f32[:nfft])) ** 2
    freqs = np.fft.rfftfreq(nfft, 1.0 / samplerate)
    total = float(mag2.sum()) + 1.0
    bass_e   = float(mag2[freqs <  300].sum())
    mid_e    = float(mag2[(freqs >= 300) & (freqs < 3000)].sum())
    treble_e = float(mag2[freqs >= 3000].sum())
    gain = rms * 14.0
    return (min(1.0, (bass_e   / total) ** 0.5 * gain),
            min(1.0, (mid_e    / total) ** 0.5 * gain),
            min(1.0, (treble_e / total) ** 0.5 * gain))


def _compute_fft_bins(samples_int16_flat, samplerate, n_bars: int = 64):
    """Retorna lista de n_bars floats 0-1 con amplitud por banda (log-scale)."""
    n = len(samples_int16_flat)
    if n < 128:
        return [0.0] * n_bars
    f32 = samples_int16_flat.astype('float32')
    rms = float(np.sqrt(np.mean(f32 ** 2))) / 32768.0
    if rms < 2e-4:
        return [0.0] * n_bars
    nfft  = min(n, 2048)
    window = np.hanning(nfft)
    mag   = np.abs(np.fft.rfft(f32[:nfft] * window))
    freqs = np.fft.rfftfreq(nfft, 1.0 / samplerate)
    f_min, f_max = 40.0, min(samplerate / 2.0 * 0.9, 8000.0)
    log_edges = np.logspace(np.log10(f_min), np.log10(f_max), n_bars + 1)
    max_mag = float(mag.max()) + 1e-6
    bars = []
    for j in range(n_bars):
        mask = (freqs >= log_edges[j]) & (freqs < log_edges[j + 1])
        val  = float(mag[mask].mean()) / max_mag if mask.any() else 0.0
        bars.append(min(1.0, val * rms * 22.0))
    return bars

# Keywords that identify Bluetooth headset mics (HFP profile).
# Using them forces the headset into telephone-quality mode (8 kHz), which
# degrades ALL audio output.  Prefer the built-in mic when available.
_BT_MIC_KEYWORDS = ("bluetooth", "redmi", "airpod", "jabra", "sony", "bose",
                    "sennheiser", "plantronics", "poly ", "beats")

def _pick_mic_device() -> Optional[int]:
    """Return the device index of the best non-Bluetooth input device.
    Falls back to None (sounddevice default) if none found."""
    try:
        import sounddevice as _sd
        devices = _sd.query_devices()
        # prefer built-in: Microphone Array, AMD, Realtek, Intel
        for priority_kw in ("microphone array", "amd audio", "realtek", "intel"):
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    name = d["name"].lower()
                    if priority_kw in name and not any(bt in name for bt in _BT_MIC_KEYWORDS):
                        return i
        # any wired/internal mic that isn't BT
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                name = d["name"].lower()
                if not any(bt in name for bt in _BT_MIC_KEYWORDS):
                    return i
    except Exception:
        pass
    return None


def _pick_loopback_input_device(devices, output_name: str, preferred_name: str = "") -> Optional[int]:
    """Best-effort selector for legacy PortAudio loopback input devices.
    Matches by preferred name first, then by output-name similarity, then any loopback input.
    """
    output_name_l = (output_name or "").strip().lower()
    pref_l = (preferred_name or "").strip().lower()

    # 1) preferred explicit name
    if pref_l:
        for i, d in enumerate(devices):
            name_i = str(d.get("name", "")).lower()
            in_ch = int(d.get("max_input_channels", 0) or 0)
            if in_ch > 0 and pref_l in name_i:
                return i

    # 2) loopback device that resembles default output
    for i, d in enumerate(devices):
        name_i = str(d.get("name", "")).lower()
        in_ch = int(d.get("max_input_channels", 0) or 0)
        if in_ch <= 0:
            continue
        if "loopback" in name_i and output_name_l and (output_name_l in name_i or name_i in output_name_l):
            return i

    # 3) any loopback input
    for i, d in enumerate(devices):
        name_i = str(d.get("name", "")).lower()
        in_ch = int(d.get("max_input_channels", 0) or 0)
        if in_ch > 0 and "loopback" in name_i:
            return i

    return None

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
_ACTION_PROMISE_RE = re.compile(
    r"\b(?:"
    r"voy\s+a|vamos\s+a|proceder[ée]\s+a|"
    r"buscar[ée]|descargar[ée]|abrir[ée]|enviar[ée]|reproducir[ée]|"
    r"crear[ée]|subir[ée]|bajar[ée]|comprobar[ée]|revisar[ée]|"
    r"har[ée]|intentar[ée]|d[ée]jame|ahora\s+mismo|"
    r"i(?:'ll|\s+will)|let\s+me|i(?:'m|\s+am)\s+going\s+to"
    r")\b",
    re.IGNORECASE,
)
_INTERNAL_TOOL_RECOVERY_MARKER = "[INTERNAL TOOL RECOVERY]"

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


def _promised_action_without_tool(text: str) -> bool:
    return bool(text and _ACTION_PROMISE_RE.search(text))

TOOL_DECLARATIONS = [
    {
        "name": "capabilities_catalog",
        "description": (
            "Use this when the user asks what Jarvis can do, asks for capabilities, available functions, "
            "a list of tools, examples of commands, or says 'que puedes hacer'. Returns an extensive categorized list."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "format": {"type": "STRING", "description": "full (default) or compact"}
            },
            "required": []
        }
    },
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "utility_tools",
        "description": (
            "General utility shortcuts. Use for opening download folders, cleaning partial download files, "
            "or getting a compact summarized web search. "
            "Actions: download_open_folder | download_cleanup | web_search_summary."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "download_open_folder | download_cleanup | web_search_summary"},
                "query": {"type": "STRING", "description": "Search query for web_search_summary"},
                "kind": {"type": "STRING", "description": "downloads | audio | video | all"},
                "folder": {"type": "STRING", "description": "Alias for kind"},
                "dry_run": {"type": "BOOLEAN", "description": "Preview cleanup without deleting files"},
                "limit": {"type": "INTEGER", "description": "Maximum cleanup scan count or search result count"},
                "max_results": {"type": "INTEGER", "description": "Maximum search results for web_search_summary"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via Telegram, Signal, Discord, Instagram or other NON-WhatsApp platforms. Do NOT use this for WhatsApp — use the 'whatsapp' tool instead.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: Telegram, Discord, Signal, Instagram (NOT WhatsApp)"}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },

    {
        "name": "whatsapp",
        "description": (
            "THE ONLY tool for ALL WhatsApp operations. "
            "Use this to: send messages, send files/attachments, read conversations, list unread messages. "
            "Accepts plain contact names like 'Rafa' or 'Mama' — resolves automatically. "
            "Actions: send (send a NEW message), send_file (send a file/attachment such as an image, PDF, video or document), "
            "get_conversation (read/search chat history with a contact), "
            "list_pending (list unread incoming messages), open_chat (open WhatsApp mode for a specific chat), "
            "start_auto_reply (automatically reply to one contact for a limited time), "
            "stop_auto_reply, list_auto_replies."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING",  "description": "send | send_file | get_conversation | list_pending | open_chat | whatsapp_mode | start_auto_reply | stop_auto_reply | list_auto_replies"},
                "contact": {"type": "STRING",  "description": "Only the recipient name (e.g. 'Rafa') or WhatsApp id"},
                "to":      {"type": "STRING",  "description": "Alias for contact when sending"},
                "body":    {"type": "STRING",  "description": "Only the exact text to send. For 'Dile a Rafa que llego tarde', body is 'llego tarde', never the full command. For send_file it is the optional caption."},
                "path":    {"type": "STRING",  "description": "Absolute local file path to attach. Required for action=send_file."},
                "limit":   {"type": "INTEGER", "description": "Max messages to return for get_conversation (default 50)"},
                "minutes": {"type": "NUMBER", "description": "Duration in minutes for start_auto_reply"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "google_calendar",
        "description": (
            "Manage Google Calendar. Use for: listing upcoming events, creating events, "
            "deleting events, searching events. "
            "Actions: list_events | create_event | delete_event | search_events."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING",  "description": "list_events | create_event | delete_event | search_events"},
                "summary":     {"type": "STRING",  "description": "Event title (create_event)"},
                "start":       {"type": "STRING",  "description": "Start date/time, natural language ok e.g. 'tomorrow at 3pm'"},
                "end":         {"type": "STRING",  "description": "End date/time (optional, defaults to 1h after start)"},
                "description": {"type": "STRING",  "description": "Event description"},
                "location":    {"type": "STRING",  "description": "Event location"},
                "event_id":    {"type": "STRING",  "description": "Event ID for delete_event"},
                "query":       {"type": "STRING",  "description": "Search query for search_events"},
                "limit":       {"type": "INTEGER", "description": "Max events to return (default 10)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "gmail",
        "description": (
            "Manage Gmail. Use for: reading emails, listing inbox, searching emails, sending emails. "
            "Actions: list_emails | search_emails | read_email | send_email."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING",  "description": "list_emails | search_emails | read_email | send_email"},
                "query":       {"type": "STRING",  "description": "Search query for search_emails (Gmail syntax supported)"},
                "email_id":    {"type": "STRING",  "description": "Email ID for read_email"},
                "to":          {"type": "STRING",  "description": "Recipient email address for send_email"},
                "subject":     {"type": "STRING",  "description": "Subject for send_email"},
                "body":        {"type": "STRING",  "description": "Body text for send_email"},
                "label":       {"type": "STRING",  "description": "Gmail label (default: INBOX)"},
                "unread_only": {"type": "BOOLEAN", "description": "Only list unread emails (list_emails)"},
                "count":       {"type": "INTEGER", "description": "Max emails to return (default 10)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "google_drive",
        "description": (
            "Manage Google Drive. Use for: listing/searching files, uploading local files, downloading Drive files, "
            "creating folders, sharing files with people or public links, renaming, replacing/updating, deleting/trashing, "
            "and getting file info. Default share permission is read-only. "
            "Actions: list_files | search_files | upload_file | download_file | create_folder | share_file | "
            "rename_file | update_file | replace_file | delete_file | trash_file | get_file_info."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list_files | search_files | upload_file | download_file | create_folder | share_file | rename_file | update_file | replace_file | delete_file | trash_file | get_file_info"},
                "query":       {"type": "STRING", "description": "File name search term when file_id is unknown"},
                "name":        {"type": "STRING", "description": "File/folder name or search alias"},
                "file_id":     {"type": "STRING", "description": "Google Drive file ID"},
                "id":          {"type": "STRING", "description": "Alias for file_id"},
                "file_path":   {"type": "STRING", "description": "Absolute local path for upload_file or replacement content for update_file"},
                "local_path":  {"type": "STRING", "description": "Alias for replacement local file path"},
                "output_dir":  {"type": "STRING", "description": "Local folder for download_file"},
                "folder_name": {"type": "STRING", "description": "Drive folder name (upload_file / create_folder)"},
                "folder_id":   {"type": "STRING", "description": "Drive folder ID"},
                "parent_id":   {"type": "STRING", "description": "Parent folder ID for create_folder"},
                "email":       {"type": "STRING", "description": "Email to share with"},
                "to":          {"type": "STRING", "description": "Alias for email"},
                "role":        {"type": "STRING", "description": "reader | commenter | writer. Default reader"},
                "anyone":      {"type": "BOOLEAN", "description": "Create public link permission instead of sharing with one email"},
                "notify":      {"type": "BOOLEAN", "description": "Send Google notification email when sharing"},
                "new_name":    {"type": "STRING", "description": "New file name for rename/update/upload-as"},
                "description": {"type": "STRING", "description": "Drive file description metadata for update_file"},
                "permanent":   {"type": "BOOLEAN", "description": "Permanently delete instead of moving to trash"},
                "export_mime": {"type": "STRING", "description": "Export MIME for Google Docs/Sheets/Slides download"},
                "mime_type":   {"type": "STRING", "description": "Optional MIME filter for search_files"},
                "count":       {"type": "INTEGER","description": "Max results (default 20)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "productivity_tools",
        "description": (
            "Quick productivity summaries and searches. Use for recent/search WhatsApp messages, today's/next/free-busy calendar, "
            "and concise Gmail inbox summaries. Actions: whatsapp_recent | whatsapp_search | calendar_today | calendar_next | "
            "calendar_freebusy | email_summary."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "whatsapp_recent | whatsapp_search | calendar_today | calendar_next | calendar_freebusy | email_summary"},
                "query": {"type": "STRING", "description": "Search text for whatsapp_search"},
                "contact": {"type": "STRING", "description": "Optional WhatsApp contact for whatsapp_search"},
                "to": {"type": "STRING", "description": "Alias for contact"},
                "calendar_id": {"type": "STRING", "description": "Calendar ID, default primary"},
                "label": {"type": "STRING", "description": "Gmail label, default INBOX"},
                "unread_only": {"type": "BOOLEAN", "description": "Only unread emails for email_summary"},
                "limit": {"type": "INTEGER", "description": "Maximum results"},
                "count": {"type": "INTEGER", "description": "Maximum email count"},
                "days": {"type": "INTEGER", "description": "How many days back to search WhatsApp"},
                "hours": {"type": "INTEGER", "description": "Calendar free/busy window in hours"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "yt_music",
        "description": (
            "YouTube Music via headless mpv. Use for: playing songs/artists/albums, "
            "controlling playback (pause, resume, next, previous, volume, shuffle), getting current song, "
            "searching music, lyrics, artist info, album tracklist, liked songs, history, liking a song, "
            "showing queue, listing user playlists, playing playlists/liked songs, listing all songs in a playlist, "
            "extracting track names, downloading playlist audio, and autoplay control. "
            "If the user asks to download liked songs or a playlist, you may first list the songs, say a brief spoken acknowledgement, "
            "then call the download action. If a track is not found exactly, use the closest matching search result rather than stopping. "
            "For any audio download, ask the user which quality they want before downloading. "
            "Also supports checking detailed download status, previewing playlists, pausing/resuming/cancelling downloads, "
            "opening the download folder, cleaning partial downloads, retrying/resuming failed downloads, "
            "setting default audio/video quality, and downloading a specific playlist range. "
            "Actions: play | pause | play_resume | toggle_play | next | previous | volume | current_song | "
            "shuffle | like | search | lyrics | artist_info | album_info | liked_songs | history | like_song | "
            "queue | show_queue | list_playlists | my_playlists | play_playlist | play_liked | autoplay | autoplay_on | autoplay_off | "
            "list_playlist_tracks | playlist_tracks | playlist_names | track_names | download_playlist_audio | download_audio_tracks | "
            "download_liked_audio | download_liked_songs_audio | download_status | download_status_verbose | playlist_preview | "
            "download_pause | download_resume | download_cancel_all | open_download_folder | retry_failed_downloads | download_resume_failed | "
            "cleanup_partial_downloads | set_default_quality | download_selected_range | download_playlist_range | queue_playlist_download."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING",  "description": "play | pause | play_resume | toggle_play | next | previous | volume | current_song | shuffle | like | search | lyrics | artist_info | album_info | liked_songs | history | like_song | queue | show_queue | list_playlists | my_playlists | play_playlist | play_liked | autoplay | autoplay_on | autoplay_off | list_playlist_tracks | playlist_tracks | playlist_names | track_names | download_playlist_audio | download_audio_tracks | download_liked_audio | download_liked_songs_audio | download_status | download_status_verbose | playlist_preview | download_pause | download_resume | download_cancel_all | open_download_folder | retry_failed_downloads | download_resume_failed | cleanup_partial_downloads | set_default_quality | download_selected_range | download_playlist_range | queue_playlist_download"},
                "query":  {"type": "STRING",  "description": "Song/artist/album name or search query"},
                "type":   {"type": "STRING",  "description": "For play/search: song (default) | artist | album"},
                "level":  {"type": "INTEGER", "description": "Volume level 0-100 (for action=volume)"},
                "shuffle":{"type": "BOOLEAN", "description": "Shuffle queue for play_playlist/play_liked"},
                "enabled":{"type": "BOOLEAN", "description": "Enable/disable autoplay or pause state for download_pause"},
                "paused":{"type": "BOOLEAN", "description": "Pause state for download_pause"},
                "playlist":{"type": "STRING", "description": "Playlist name or ID for playlist track listing / downloads"},
                "playlist_id":{"type": "STRING", "description": "Explicit playlist ID"},
                "start_index":{"type": "INTEGER", "description": "Start playback from this track index inside a playlist (0-based)"},
                "tracks": {
                    "type": "ARRAY",
                    "description": "Preloaded track array may be passed by code",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING"},
                            "artists": {"type": "STRING"},
                            "videoId": {"type": "STRING"},
                            "url": {"type": "STRING"}
                        }
                    }
                },
                "output_dir":{"type": "STRING", "description": "Folder where audio downloads will be saved"},
                "quality": {"type": "STRING", "description": "Download quality: low | medium | high | best. Ask the user before downloading."},
                "audio_quality": {"type": "STRING", "description": "Default audio quality for set_default_quality."},
                "video_quality": {"type": "STRING", "description": "Default video quality for set_default_quality."},
                "kind": {"type": "STRING", "description": "audio or video for status/folder/cleanup"},
                "start": {"type": "INTEGER", "description": "First playlist track index for download_selected_range/download_playlist_range, 1-based"},
                "end": {"type": "INTEGER", "description": "Last playlist track index for download_selected_range/download_playlist_range, inclusive"},
                "limit":  {"type": "INTEGER", "description": "Max results to return (default 5)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, showing trending videos, or downloading a YouTube video. "
            "If the user gives a title instead of a URL, search YouTube first and use the best match; "
            "if nothing matches exactly, try the closest relevant result instead of failing immediately. "
            "For video downloads, ask the user which quality they want before downloading. "
            "Also supports checking download status, opening the download folder, cleaning partial downloads, retrying failed downloads, "
            "setting default quality, and finding the best YouTube match for a query."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending | download_video | download_status | open_download_folder | retry_failed_downloads | cleanup_partial_downloads | set_default_quality | search_youtube_best_match (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
                "output_dir": {"type": "STRING", "description": "Folder where downloads will be saved"},
                "quality": {"type": "STRING", "description": "Download quality: low | medium | high | best. Ask the user before downloading."},
                "audio_quality": {"type": "STRING", "description": "Default audio quality for set_default_quality."},
                "video_quality": {"type": "STRING", "description": "Default video quality for set_default_quality."},
                "kind": {"type": "STRING", "description": "audio or video for status/folder/cleanup"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "system_tools",
        "description": (
            "High-level local system utilities. Use for checking PC status, launching/focusing apps, "
            "finding recent files, searching files, or revealing a file in Explorer/Finder. "
            "Actions: system_status | app_launch | app_focus | file_find | file_recent | file_reveal."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "system_status | app_launch | app_focus | file_find | file_recent | file_reveal"},
                "app_name": {"type": "STRING", "description": "Application name for app_launch/app_focus"},
                "title": {"type": "STRING", "description": "Window title fragment for app_focus"},
                "name": {"type": "STRING", "description": "File or app name"},
                "query": {"type": "STRING", "description": "Search query or app name"},
                "extension": {"type": "STRING", "description": "File extension for file_find, e.g. pdf or .pdf"},
                "path": {"type": "STRING", "description": "Base folder shortcut or full user path: home | desktop | downloads | documents | pictures | music | videos"},
                "limit": {"type": "INTEGER", "description": "Maximum result count"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "personal_tools",
        "description": (
            "Explicit personal utilities. Use when the user asks to search/list/delete memory, "
            "manage quick notes, or read/write clipboard/history. "
            "Actions: memory_list | memory_search | memory_forget | notes_add | notes_list | notes_search | "
            "clipboard_get | clipboard_set | clipboard_history."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "memory_list | memory_search | memory_forget | notes_add | notes_list | notes_search | clipboard_get | clipboard_set | clipboard_history"
                },
                "query": {"type": "STRING", "description": "Search text for memory_search, notes_search, or memory_forget"},
                "category": {"type": "STRING", "description": "Memory category: identity | preferences | projects | relationships | wishes | notes"},
                "key": {"type": "STRING", "description": "Memory key for memory_forget"},
                "title": {"type": "STRING", "description": "Optional note title for notes_add"},
                "text": {"type": "STRING", "description": "Text for notes_add or clipboard_set"},
                "body": {"type": "STRING", "description": "Alias for text"},
                "tags": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Optional note tags"},
                "limit": {"type": "INTEGER", "description": "Maximum results to return"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._download_cancel_event = threading.Event()
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None
        self._interaction_id = 0
        self._interaction_had_tool = False
        self._interaction_recovery_sent = False
        self._text_interaction_pending = False
        self._internal_recovery_active = False
        self._tool_recovery_task: asyncio.Task | None = None
        self._latest_user_request = ""

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_text_command(text),
            self._loop
        )

    def _begin_interaction(self):
        self._interaction_id += 1
        self._interaction_had_tool = False
        self._interaction_recovery_sent = False
        self._internal_recovery_active = False
        if self._tool_recovery_task and not self._tool_recovery_task.done():
            self._tool_recovery_task.cancel()
        self._tool_recovery_task = None

    async def _send_text_command(self, text: str):
        self._begin_interaction()
        self._latest_user_request = text
        self._text_interaction_pending = True
        await self.session.send_client_content(
            turns={"parts": [{"text": text}]},
            turn_complete=True,
        )

    def _schedule_tool_recovery(self, assistant_text: str):
        if (
            self._interaction_id <= 0
            or
            self._interaction_had_tool
            or self._interaction_recovery_sent
            or self._internal_recovery_active
            or not _promised_action_without_tool(assistant_text)
        ):
            return

        interaction_id = self._interaction_id

        async def recover():
            # Give a late tool-call event a moment to arrive before intervening.
            await asyncio.sleep(0.35)
            if (
                interaction_id != self._interaction_id
                or self._interaction_had_tool
                or self._interaction_recovery_sent
                or not self.session
            ):
                return

            self._interaction_recovery_sent = True
            self._internal_recovery_active = True
            original_request = self._latest_user_request[:1000]
            correction = (
                f"{_INTERNAL_TOOL_RECOVERY_MARKER} You promised to perform the user's "
                "requested action but emitted no tool call. Execute the appropriate tool "
                f"now. Original request: {original_request!r}. Use the conversation context "
                "to resolve references. Do not "
                "repeat the acknowledgement and do not ask the user to repeat the command."
            )
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": correction}]},
                    turn_complete=True,
                )
            except Exception:
                self._internal_recovery_active = False

        self._tool_recovery_task = asyncio.create_task(recover())

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        else:
            self.ui.set_audio_bands(0.0, 0.0, 0.0)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def request_download_cancel(self):
        self._download_cancel_event.set()

    def listen_for_reply(self, timeout: int = 6) -> str:
        """Record short audio from the microphone and transcribe it using existing transcription helper.
        Returns the transcribed text or empty string on failure."""
        try:
            import sounddevice as sd
            import numpy as np
            import wave
            import tempfile
            from pathlib import Path
            from actions.file_processor import _process_audio

            sr = SEND_SAMPLE_RATE
            seconds = int(timeout)
            self.ui.set_state("LISTENING")
            # record
            data = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype='float32')
            sd.wait()
            # normalize and save
            tmp = tempfile.mktemp(suffix='.wav')
            data_i16 = np.int16(data.flatten() * 32767)
            with wave.open(tmp, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(data_i16.tobytes())

            # transcribe using file_processor helper
            try:
                res = _process_audio(Path(tmp), 'transcribe', {}, speak=self.speak)
                # _process_audio returns a string; if saved file, preview included
                if isinstance(res, str):
                    # try to extract preview after 'Preview:' if present
                    if 'Preview:' in res:
                        return res.split('Preview:')[-1].strip()
                    return res
                return str(res)
            finally:
                try:
                    Path(tmp).unlink()
                except Exception:
                    pass
        except Exception as e:
            print(f"listen_for_reply failed: {e}")
            return ""

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        hist_str = _fmt_history()
        if hist_str:
            parts.append(hist_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "capabilities_catalog":
                r = await loop.run_in_executor(None, lambda: capabilities_catalog(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "personal_tools":
                r = await loop.run_in_executor(None, lambda: personal_tools(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                try:
                    import re
                    wa_wrapper = None
                    try:
                        from actions import whatsapp as wa_wrapper
                    except Exception:
                        wa_wrapper = None

                    platform = (args.get('platform') or '').lower()
                    receiver = args.get('receiver') or args.get('to') or ''
                    message_text = args.get('message_text') or args.get('body') or args.get('text') or ''

                    # Keep legacy WhatsApp calls on the same strict path as the dedicated tool.
                    looks_like_number = re.match(r"^\+?\d{7,15}$", str(receiver)) is not None
                    if wa_wrapper and ( 'whatsapp' in platform or ('@' in str(receiver)) or looks_like_number ):
                        receiver, message_text = wa_wrapper.normalize_send_request(receiver, message_text)
                        if '@' in str(receiver):
                            to = receiver
                        elif looks_like_number:
                            to = f"{receiver.lstrip('+')}@c.us"
                        else:
                            to = await loop.run_in_executor(
                                None,
                                lambda: wa_wrapper.resolve_contact(receiver, strict=True),
                            )
                        await loop.run_in_executor(None, lambda: wa_wrapper.send_whatsapp(to, message_text))
                        result = f"Mensaje enviado a {receiver}."
                    else:
                        r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                        result = r or f"Message sent to {args.get('receiver')}."
                except Exception as e:
                    result = f"Tool 'send_message' failed: {e}"

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                _action = str(args.get("action", "play")).lower().strip()
                if _action == "download_video":
                    from actions.youtube_video import download_video as _download_video
                    query_or_url = args.get("url") or args.get("query") or args.get("video_id") or ""
                    output_dir = args.get("output_dir") or args.get("path") or ""
                    quality = str(args.get("quality") or "").strip()
                    if not quality:
                        r = "Antes de descargar el video, dime qué calidad quieres: baja, media, alta o best."
                    else:
                        self._download_cancel_event.clear()
                        self.ui.set_download_state({
                            "active": True,
                            "percent": 0,
                            "label": "Starting video download",
                            "detail": str(query_or_url)[:120] or "video",
                            "can_cancel": True,
                        })
                        self.speak("Empiezo la descarga del video ahora, sir.")
                        r = await loop.run_in_executor(
                            None,
                            lambda: _download_video(
                                query_or_url,
                                output_dir=output_dir,
                                quality=quality,
                                progress_hook=self.ui.set_download_state,
                                cancel_event=self._download_cancel_event,
                            )
                        )
                        if "cancelada" in str(r).lower():
                            self.speak("Descarga cancelada, sir.")
                        elif not str(r).lower().startswith(("yt-dlp is not", "no valid")):
                            self.speak("Video descargado, sir.")
                    result = r or "Done."
                else:
                    r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui, speak=self.speak))
                    result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "utility_tools":
                r = await loop.run_in_executor(None, lambda: utility_tools(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_tools":
                r = await loop.run_in_executor(None, lambda: system_tools(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "google_calendar":
                r = await loop.run_in_executor(None, lambda: google_calendar(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "gmail":
                r = await loop.run_in_executor(None, lambda: gmail(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "google_drive":
                _drive_action = str(args.get("action", "")).lower().strip()
                _progress = self.ui.set_task_state if _drive_action in ("upload_file", "download_file", "update_file", "replace_file") else None
                r = await loop.run_in_executor(
                    None,
                    lambda: gdrive(parameters=args, player=self.ui, speak=self.speak, progress_hook=_progress)
                )
                result = r or "Done."

            elif name == "productivity_tools":
                r = await loop.run_in_executor(None, lambda: productivity_tools(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "yt_music":
                # Prefer headless backend; fall back to GUI ytmusic
                try:
                    import actions.ytmusic_headless as _hl
                    _action = args.get('action', '').lower()
                    _to_bool = lambda v: str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'on', 'si', 'sí') if not isinstance(v, bool) else v
                    if _action == 'play':
                        _q = args.get('query') or args.get('q') or ''
                        # play() is now fast (search_songs only, mpv resolves stream internally)
                        r = await loop.run_in_executor(None, lambda: _hl.play(_q))
                        print(f"[yt_music play] {r}")
                    elif _action in ('pause', 'play_pause'):
                        r = await loop.run_in_executor(None, _hl.pause)
                    elif _action in ('resume', 'play_resume'):
                        r = await loop.run_in_executor(None, _hl.resume)
                    elif _action in ('toggle_play', 'toggle'):
                        r = await loop.run_in_executor(None, _hl.toggle_play)
                    elif _action == 'stop':
                        r = await loop.run_in_executor(None, _hl.stop)
                    elif _action in ('next', 'next_track'):
                        r = await loop.run_in_executor(None, _hl.next)
                    elif _action in ('previous', 'prev', 'previous_track'):
                        r = await loop.run_in_executor(None, _hl.previous)
                    elif _action == 'volume':
                        lvl = int(args.get('level', 50))
                        r = await loop.run_in_executor(None, lambda: _hl.volume(lvl))
                        try:
                            self.ui.set_music_volume(lvl)
                        except Exception:
                            pass
                    elif _action == 'seek':
                        sec = int(args.get('seconds', 0) or args.get('position', 0))
                        r = await loop.run_in_executor(None, lambda: _hl.seek(sec))
                    elif _action == 'current_song':
                        r = str(_hl.current())
                    elif _action in ('queue', 'show_queue', 'current_queue'):
                        _lim = int(args.get('limit', 20) or 20)
                        r = await loop.run_in_executor(None, lambda: _hl.show_queue(_lim))
                    elif _action in ('autoplay_on',):
                        r = await loop.run_in_executor(None, lambda: _hl.set_autoplay(True))
                    elif _action in ('autoplay_off',):
                        r = await loop.run_in_executor(None, lambda: _hl.set_autoplay(False))
                    elif _action in ('autoplay',):
                        _enabled = args.get('enabled', True)
                        r = await loop.run_in_executor(None, lambda: _hl.set_autoplay(_enabled))
                    elif _action in ('list_playlists', 'my_playlists'):
                        _lim = int(args['limit']) if args.get('limit') not in (None, "", 0, "0") else None
                        r = await loop.run_in_executor(None, lambda: _hl.list_playlists(_lim))
                    elif _action in ('play_playlist',):
                        _q = args.get('query') or args.get('playlist') or args.get('playlist_id') or ''
                        _lim = int(args['limit']) if args.get('limit') not in (None, "", 0, "0") else None
                        _shf = _to_bool(args.get('shuffle', False))
                        _start = int(args.get('start_index', 0) or 0)
                        r = await loop.run_in_executor(None, lambda: _hl.play_playlist(_q, _lim, _shf, _start))
                    elif _action in ('play_liked',):
                        _lim = int(args['limit']) if args.get('limit') not in (None, "", 0, "0") else None
                        _shf = _to_bool(args.get('shuffle', False))
                        r = await loop.run_in_executor(None, lambda: _hl.play_liked(_lim, _shf))
                    elif _action in ('download_status',):
                        from actions.ytmusic import download_status as _download_status
                        r = await loop.run_in_executor(None, _download_status)
                    elif _action in ('download_status_verbose',):
                        from actions.ytmusic import download_status_verbose as _download_status_verbose
                        r = await loop.run_in_executor(None, _download_status_verbose)
                    elif _action in ('playlist_preview',):
                        from actions.ytmusic import playlist_preview as _playlist_preview
                        _q = args.get('query') or args.get('playlist') or args.get('playlist_id') or ''
                        _lim = int(args.get('limit', 5) or 5)
                        r = await loop.run_in_executor(None, lambda: _playlist_preview(_q, _lim))
                    elif _action in ('download_pause', 'download_resume', 'download_unpause'):
                        from actions.ytmusic import download_pause as _download_pause
                        _paused = False if _action in ('download_resume', 'download_unpause') else _to_bool(args.get('enabled', args.get('paused', True)))
                        r = await loop.run_in_executor(None, lambda: _download_pause(_paused))
                        self.speak("Descarga pausada, sir." if _paused else "Descarga reanudada, sir.")
                    elif _action in ('download_cancel_all',):
                        from actions.ytmusic import download_cancel_all as _download_cancel_all
                        self._download_cancel_event.set()
                        r = await loop.run_in_executor(None, _download_cancel_all)
                        self.ui.set_download_state({
                            "active": False,
                            "percent": 0,
                            "label": "Download cancelled",
                            "detail": "All queued downloads cancelled",
                            "can_cancel": False,
                        })
                        self.speak("Cancelo las descargas, sir.")
                    elif _action in ('open_download_folder',):
                        from actions.ytmusic import open_download_folder as _open_download_folder
                        _kind = args.get('kind') or 'audio'
                        r = await loop.run_in_executor(None, lambda: _open_download_folder(_kind))
                    elif _action in ('cleanup_partial_downloads',):
                        from actions.ytmusic import cleanup_partial_downloads as _cleanup_partial_downloads
                        _kind = args.get('kind') or 'audio'
                        removed = await loop.run_in_executor(None, lambda: _cleanup_partial_downloads(_kind))
                        r = f"Removed {len(removed)} partial download file(s)."
                    elif _action in ('set_default_quality',):
                        from actions.ytmusic import set_default_quality as _set_default_quality
                        _aq = args.get('audio_quality') or args.get('quality') or ''
                        _vq = args.get('video_quality') or ''
                        r = await loop.run_in_executor(None, lambda: _set_default_quality(_aq, _vq))
                    elif _action in ('retry_failed_downloads', 'download_resume_failed'):
                        from actions.ytmusic import download_resume_failed as _download_resume_failed, retry_failed_downloads as _retry_failed_downloads
                        _out = args.get('output_dir') or args.get('path') or ''
                        self._download_cancel_event.clear()
                        self.speak("Reintento las descargas fallidas ahora, sir.")
                        _runner = _download_resume_failed if _action == 'download_resume_failed' else _retry_failed_downloads
                        files = await loop.run_in_executor(
                            None,
                            lambda: _runner(
                                output_dir=_out,
                                progress_hook=self.ui.set_download_state,
                                cancel_event=self._download_cancel_event,
                            )
                        )
                        r = f"Retried failed downloads. Saved {len(files)} file(s)."
                    elif _action in ('download_selected_range', 'download_playlist_range'):
                        from actions.ytmusic import download_playlist_range as _download_playlist_range
                        _q = args.get('query') or args.get('playlist') or args.get('playlist_id') or ''
                        _out = args.get('output_dir') or args.get('path') or ''
                        _quality = str(args.get('quality') or '').strip()
                        if not _quality:
                            r = "Antes de descargar ese rango, dime qué calidad quieres: baja, media, alta o best."
                        else:
                            _start = int(args.get('start') or 1)
                            _end = int(args.get('end') or args.get('limit') or _start)
                            self._download_cancel_event.clear()
                            self.ui.set_download_state({
                                "active": True,
                                "percent": 0,
                                "label": "Starting selected range",
                                "detail": f"{_q} · {_start}-{_end}",
                                "can_cancel": True,
                            })
                            self.speak("Empiezo la descarga de ese rango ahora, sir.")
                            files = await loop.run_in_executor(
                                None,
                                lambda: _download_playlist_range(
                                    query_or_id=_q,
                                    start=_start,
                                    end=_end,
                                    output_dir=_out,
                                    quality=_quality,
                                    progress_hook=self.ui.set_download_state,
                                    cancel_event=self._download_cancel_event,
                                )
                            )
                            r = "Descarga cancelada." if self._download_cancel_event.is_set() else f"Downloaded {len(files)} selected audio file(s)."
                    elif _action in ('queue_playlist_download',):
                        from actions.ytmusic import queue_playlist_download as _queue_playlist_download
                        _q = args.get('query') or args.get('playlist') or args.get('playlist_id') or ''
                        _lim = int(args.get('limit', 1000) or 1000)
                        _out = args.get('output_dir') or args.get('path') or ''
                        _shf = _to_bool(args.get('shuffle', False))
                        _quality = str(args.get('quality') or '').strip()
                        if not _quality:
                            r = "Antes de meter la playlist en cola, dime qué calidad quieres: baja, media, alta o best."
                        else:
                            self._download_cancel_event.clear()
                            r = await loop.run_in_executor(
                                None,
                                lambda: _queue_playlist_download(
                                    query_or_id=_q,
                                    limit=_lim,
                                    output_dir=_out,
                                    shuffle=_shf,
                                    quality=_quality,
                                    progress_hook=self.ui.set_download_state,
                                    cancel_event=self._download_cancel_event,
                                )
                            )
                            self.speak("Playlist añadida a la cola de descarga, sir.")
                    elif _action in ('download_liked_audio', 'download_liked_songs_audio'):
                        from actions.ytmusic import download_liked_audio as _download_liked_audio
                        _lim = int(args.get('limit', 25) or 25)
                        _out = args.get('output_dir') or args.get('path') or ''
                        _shf = _to_bool(args.get('shuffle', False))
                        _quality = str(args.get('quality') or '').strip()
                        if not _quality:
                            r = "Antes de descargar las canciones guardadas, dime qué calidad quieres: baja, media, alta o best."
                        else:
                            self._download_cancel_event.clear()
                            self.ui.set_download_state({
                                "active": True,
                                "percent": 0,
                                "label": "Starting audio download",
                                "detail": f"Liked songs · {_lim}",
                                "can_cancel": True,
                            })
                            self.speak("Empiezo la descarga ahora, sir.")
                            files = await loop.run_in_executor(
                                None,
                                lambda: _download_liked_audio(
                                    _lim,
                                    _out,
                                    _shf,
                                    _quality,
                                    progress_hook=self.ui.set_download_state,
                                    cancel_event=self._download_cancel_event,
                                )
                            )
                            result_dir = _out or str((Path.home() / 'Downloads' / 'JARVIS_Audio'))
                            if self._download_cancel_event.is_set():
                                r = "Descarga cancelada."
                                self.speak("Descarga cancelada, sir.")
                            else:
                                r = f"Downloaded {len(files)} liked song(s) to {result_dir}."
                                self.speak("Descarga completada, sir.")
                    elif _action in ('download_playlist_audio', 'download_audio_tracks'):
                        from actions.ytmusic import download_playlist_audio as _download_playlist_audio, _playlist_output_dir as _playlist_output_dir
                        _q = args.get('query') or args.get('playlist') or args.get('playlist_id') or ''
                        _lim = int(args.get('limit', 1000) or 1000)
                        _out = args.get('output_dir') or args.get('path') or ''
                        _shf = _to_bool(args.get('shuffle', False))
                        _quality = str(args.get('quality') or '').strip()
                        if not _quality:
                            r = "Antes de descargar la playlist, dime qué calidad quieres: baja, media, alta o best."
                        else:
                            self._download_cancel_event.clear()
                            self.ui.set_download_state({
                                "active": True,
                                "percent": 0,
                                "label": "Starting audio download",
                                "detail": str(_q)[:120] or "playlist",
                                "can_cancel": True,
                            })
                            self.speak("Empiezo la descarga ahora, sir.")
                            files = await loop.run_in_executor(
                                None,
                                lambda: _download_playlist_audio(
                                    _q,
                                    _lim,
                                    _out,
                                    _shf,
                                    _quality,
                                    progress_hook=self.ui.set_download_state,
                                    cancel_event=self._download_cancel_event,
                                )
                            )
                            try:
                                result_dir = str(_playlist_output_dir(_q, _out))
                            except Exception:
                                result_dir = _out or str((Path.home() / 'Downloads' / 'JARVIS_Audio'))
                            if self._download_cancel_event.is_set():
                                r = "Descarga cancelada."
                                self.speak("Descarga cancelada, sir.")
                            else:
                                r = f"Downloaded {len(files)} audio file(s) to {result_dir}."
                                self.speak("Descarga completada, sir.")
                    else:
                        # unknown action: fall through to GUI backend
                        raise ImportError('unknown action for headless')
                except Exception as _hl_exc:
                    import traceback
                    print(f"[yt_music headless] fallback to GUI: {_hl_exc}")
                    traceback.print_exc()
                    r = await loop.run_in_executor(None, lambda: ytmusic(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "whatsapp":
                # whatsapp tool: use UI's whatsapp_manager or fallback to actions.whatsapp
                action = args.get('action')
                try:
                    mgr = getattr(self.ui, 'whatsapp_manager', None)
                    from actions import whatsapp as wa_wrapper
                except Exception:
                    mgr = None
                    wa_wrapper = None

                # Ensure bridge is running and authenticated; show QR dialog if needed
                if wa_wrapper:
                    ready = await loop.run_in_executor(None, wa_wrapper.ensure_bridge_ready)
                    if not ready:
                        return types.FunctionResponse(
                            id=fc.id, name=name,
                            response={"result": "WhatsApp not connected. Please scan the QR code and try again."}
                        )

                if action == 'send':
                    contact_raw = args.get('contact') or args.get('to') or args.get('receiver') or ''
                    body = args.get('body') or args.get('message_text') or args.get('text') or ''
                    try:
                        if not wa_wrapper:
                            raise RuntimeError("El módulo de WhatsApp no está disponible.")
                        contact_raw, body = wa_wrapper.normalize_send_request(contact_raw, body)
                        if not contact_raw:
                            raise wa_wrapper.ContactNotFound("No se indicó el contacto.")
                        if not body:
                            raise wa_wrapper.WhatsAppError("El mensaje está vacío.")
                        if '@' in contact_raw:
                            to_id = contact_raw
                        else:
                            to_id = await loop.run_in_executor(
                                None,
                                lambda: wa_wrapper.resolve_contact(contact_raw, strict=True),
                            )
                        sent = await loop.run_in_executor(
                            None,
                            lambda: wa_wrapper.send_whatsapp(to_id, body),
                        )
                        result = f"Mensaje enviado a {contact_raw}."
                        if isinstance(sent, dict) and not sent.get("ok"):
                            raise wa_wrapper.WhatsAppError("WhatsApp no confirmó el envío.")
                    except Exception as e:
                        result = f"No se pudo enviar el mensaje: {e}"
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": result, "silent": False}
                    )

                elif action == 'send_file':
                    contact_raw = args.get('contact') or args.get('to') or args.get('receiver') or ''
                    file_path = args.get('path') or args.get('file_path') or args.get('file') or ''
                    caption = args.get('body') or args.get('caption') or args.get('text') or ''
                    try:
                        if not wa_wrapper:
                            raise RuntimeError("El módulo de WhatsApp no está disponible.")
                        if not contact_raw:
                            raise wa_wrapper.ContactNotFound("No se indicó el contacto.")
                        if not file_path:
                            raise wa_wrapper.WhatsAppError("No se indicó el archivo a enviar.")
                        if '@' in contact_raw:
                            to_id = contact_raw
                        else:
                            to_id = await loop.run_in_executor(
                                None,
                                lambda: wa_wrapper.resolve_contact(contact_raw, strict=True),
                            )
                        sent = await loop.run_in_executor(
                            None,
                            lambda: wa_wrapper.send_whatsapp_media(to_id, file_path, caption),
                        )
                        result = f"Archivo enviado a {contact_raw}."
                        if isinstance(sent, dict) and not sent.get("ok"):
                            raise wa_wrapper.WhatsAppError("WhatsApp no confirmó el envío del archivo.")
                    except Exception as e:
                        result = f"No se pudo enviar el archivo: {e}"
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": result, "silent": False}
                    )

                elif action in ('open_chat', 'whatsapp_mode'):
                    contact = args.get('contact') or args.get('to') or args.get('receiver') or args.get('query') or ''
                    if not contact:
                        result = "Necesito el contacto para abrir el modo WhatsApp."
                    else:
                        self.ui.open_whatsapp_chat(contact)
                        result = f"Modo WhatsApp abierto para {contact}."

                elif action == 'list_pending':
                    if mgr:
                        pend = mgr.list_pending()
                        result = pend
                    else:
                        result = "no manager available"

                elif action == 'start_auto_reply':
                    contact = args.get('contact') or args.get('to') or ''
                    minutes = args.get('minutes')
                    if not mgr:
                        result = "El gestor de WhatsApp no está disponible."
                    elif not contact or minutes is None:
                        result = "Necesito el contacto y la duración en minutos."
                    else:
                        try:
                            session = await loop.run_in_executor(
                                None,
                                lambda: mgr.start_auto_reply(contact, float(minutes)),
                            )
                            result = (
                                f"Respuesta automática activada para {contact} durante "
                                f"{session['minutes']:g} minutos."
                            )
                        except Exception as e:
                            result = f"No se pudo activar la respuesta automática: {e}"

                elif action == 'stop_auto_reply':
                    contact = args.get('contact') or args.get('to') or ''
                    if not mgr:
                        result = "El gestor de WhatsApp no está disponible."
                    else:
                        try:
                            stopped = await loop.run_in_executor(
                                None,
                                lambda: mgr.stop_auto_reply(contact),
                            )
                            result = (
                                "Respuesta automática desactivada."
                                if stopped
                                else "No había ninguna respuesta automática activa."
                            )
                        except Exception as e:
                            result = f"No se pudo desactivar la respuesta automática: {e}"

                elif action == 'list_auto_replies':
                    result = mgr.list_auto_replies() if mgr else []

                elif action == 'prepare_reply':
                    mid = args.get('message_id')
                    text = args.get('body') or args.get('text')
                    if not mid or not text:
                        result = 'missing message_id or text'
                    else:
                        try:
                            mgr.prepare_reply(mid, text)
                            result = 'ok'
                        except Exception as e:
                            result = f'prepare failed: {e}'

                elif action == 'send_reply':
                    mid = args.get('message_id')
                    if not mid:
                        result = 'missing message_id'
                    else:
                        try:
                            resp = mgr.send_reply(mid)
                            result = resp
                        except Exception as e:
                            result = f'send_reply failed: {e}'

                elif action == 'get_message':
                    mid = args.get('message_id')
                    if not mid:
                        result = 'missing message_id'
                    else:
                        result = mgr.get(mid)

                elif action == 'get_conversation':
                    # Fetch recent conversation with a contact (by name or id)
                    contact = args.get('contact') or args.get('to') or args.get('receiver')
                    limit = int(args.get('limit') or 50)
                    try:
                        from actions import whatsapp as wa_wrapper
                        # try to resolve and fetch via bridge
                        conv = await loop.run_in_executor(
                            None,
                            lambda: wa_wrapper.get_conversation(contact, limit, strict=True),
                        )
                        # return only essential fields to avoid huge payloads
                        simplified = [
                            {"id": m.get('id'), "from": m.get('from'), "to": m.get('to'), "body": m.get('body'), "timestamp": m.get('timestamp')} for m in conv
                        ]
                        result = simplified
                    except Exception as e:
                        result = f'get_conversation failed: {e}'

                else:
                    result = f'unknown whatsapp action: {action}'

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(0.35)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )


        try:
            mic_device = _pick_mic_device()
            if mic_device is not None:
                import sounddevice as _sd
                print(f"[JARVIS] 🎤 Using mic: {_sd.query_devices(mic_device)['name']}")
            with sd.InputStream(
                device=mic_device,
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                if not in_buf and not self._internal_recovery_active:
                                    if self._text_interaction_pending:
                                        self._text_interaction_pending = False
                                    else:
                                        self._begin_interaction()
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            internal_input = _INTERNAL_TOOL_RECOVERY_MARKER in full_in
                            if full_in and not internal_input:
                                self._latest_user_request = full_in
                            if full_in and not internal_input:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []
                            self._text_interaction_pending = False

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                            # Persistir turno para historial entre sesiones
                            if (full_in and not internal_input) or full_out:
                                try:
                                    _save_turn("" if internal_input else full_in, full_out)
                                except Exception:
                                    pass

                            if not internal_input:
                                self._schedule_tool_recovery(full_out)

                    if response.tool_call:
                        self._interaction_had_tool = True
                        self._internal_recovery_active = False
                        if self._tool_recovery_task and not self._tool_recovery_task.done():
                            self._tool_recovery_task.cancel()
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                # alimentar amplitud del TTS al orbe
                try:
                    arr = np.frombuffer(chunk, dtype=np.int16)
                    b, m, tr = _compute_bands(arr, RECEIVE_SAMPLE_RATE)
                    self.ui.set_audio_bands(b, m, tr)
                except Exception:
                    pass
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._turn_done_event = asyncio.Event()

                    print("[JARVIS] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()
            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    from actions.whatsapp_bridge_process import start_bridge, stop_bridge

    bridge_started = start_bridge()
    if not bridge_started:
        print("[JARVIS] WhatsApp bridge could not be started.")

    ui = JarvisUI("face.png")
    ui._app.aboutToQuit.connect(stop_bridge)

    # create WhatsApp manager early so it can poll messages; callback set later
    try:
        from actions.whatsapp_manager import WhatsAppManager
        mgr = WhatsAppManager()
        ui.whatsapp_manager = mgr
        try:
            ui._win.whatsapp_manager = mgr
        except Exception:
            pass
    except Exception:
        ui.whatsapp_manager = None

    def runner():
        ui.wait_for_api_key()
        # Onboarding: if no Google account is connected yet, guide the user
        # through creating their OAuth credentials and signing in (single flow
        # for Calendar + Gmail + Drive + YouTube). Non-blocking — the dialog is
        # marshalled to the Qt main thread by the auth-dialog poller.
        try:
            from actions.google_auth import has_credentials, is_signed_in
            if not has_credentials() or not is_signed_in():
                from actions.auth_dialog import show_google_setup_dialog
                threading.Thread(target=show_google_setup_dialog, daemon=True).start()
        except Exception:
            pass
        jarvis = JarvisLive(ui)
        # expose jarvis instance globally so playback handlers can call speak if needed
        globals()['JARVIS_INSTANCE'] = jarvis
        ui.on_download_cancel = jarvis.request_download_cancel
        # connect whatsapp manager to jarvis.speak to announce incoming messages
        try:
            mgr = getattr(ui, 'whatsapp_manager', None)
            if mgr is not None:
                def _announce(entry):
                    """Announce incoming WA message through Gemini TTS (no auto-reply)."""
                    try:
                        chat_id   = entry.get('from', '')
                        author_id = entry.get('author') or ''
                        sender_nm = entry.get('senderName') or ''
                        body = (entry.get('body') or '').replace('\n', ' ').strip()[:160]
                        try:
                            from actions.whatsapp import get_contact_name
                            if '@g.us' in chat_id:
                                # mensaje de grupo
                                group_name = get_contact_name(chat_id) or chat_id.split('@')[0]
                                if sender_nm:
                                    display = f"{sender_nm} (grupo: {group_name})"
                                elif author_id:
                                    person = get_contact_name(author_id) or author_id.split('@')[0]
                                    display = f"{person} (grupo: {group_name})"
                                else:
                                    display = f"alguien en {group_name}"
                            else:
                                display = get_contact_name(chat_id) or chat_id.split('@')[0]
                        except Exception:
                            display = sender_nm or chat_id.split('@')[0]
                        # Log in UI
                        ui.write_log(f"[WhatsApp] {display}: {body}")
                        # Send to Gemini as a notification — model will read it aloud.
                        # Prefix tells the model this is a passive notification, not a command.
                        jarvis.speak(
                            f"[NOTIFICACIÓN WHATSAPP — solo lee esto en voz alta, no llames ninguna herramienta] "
                            f"{display} te ha escrito: {body}"
                        )
                    except Exception:
                        pass
                mgr.on_new_message = _announce
        except Exception:
            pass
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
        
    # Conectar la barra de reproducción a los handlers de ytmusic
    def _install_playback_handlers():
        try:
            import threading
            # Prefer headless backend if available
            try:
                import actions.ytmusic_headless as ymod
                _HEADLESS = True
            except Exception:
                import actions.ytmusic as ymod
                _HEADLESS = False

            # --- loopback: capturar audio del sistema para el visualizador ---
            _lb_stop  = threading.Event()
            _lb_thread_ref = [None]

            def _loopback_worker():
                """Captura WASAPI loopback y alimenta el visualizador con el audio de música."""
                fft_lock = threading.Lock()
                fft_ready = threading.Event()
                fft_latest = [None]

                def _publish_audio_samples(samples, sample_rate):
                    # Audio callbacks must stay non-blocking. Keep only the newest block.
                    with fft_lock:
                        fft_latest[0] = (samples, sample_rate)
                    fft_ready.set()

                def _fft_worker():
                    while not _lb_stop.is_set():
                        if not fft_ready.wait(0.15):
                            continue
                        fft_ready.clear()
                        with fft_lock:
                            item = fft_latest[0]
                            fft_latest[0] = None
                        if item is None:
                            continue
                        samples, sample_rate = item
                        try:
                            ui.set_fft_bins(_compute_fft_bins(samples, sample_rate))
                        except Exception:
                            pass

                threading.Thread(
                    target=_fft_worker,
                    name="jarvis-fft",
                    daemon=True,
                ).start()

                try:
                    import sounddevice as _sd
                    out_idx = _sd.default.device[1]
                    if out_idx is None or out_idx < 0:
                        print("[JARVIS] ⚠️ Loopback: no hay dispositivo de salida por defecto")
                        return
                    dev_info = _sd.query_devices(out_idx)
                    native_sr = int(dev_info.get('default_samplerate', 44100))
                    native_ch = max(1, min(2, int(dev_info.get('max_output_channels', 2))))
                    out_name = str(dev_info.get('name', '')).strip()
                    pref_name = os.getenv("JARVIS_LOOPBACK_DEVICE", "").strip()
                    loop_dev = out_idx
                    extra = None

                    # Ruta 1 (versiones nuevas): WasapiSettings(loopback=True)
                    try:
                        extra = _sd.WasapiSettings(loopback=True)
                    except Exception:
                        # Ruta 2 (versiones viejas): usar dispositivo input "loopback"
                        devices = _sd.query_devices()
                        loop_dev = _pick_loopback_input_device(devices, out_name, pref_name)
                        if loop_dev is not None:
                            d = _sd.query_devices(loop_dev)
                            native_sr = int(d.get('default_samplerate', native_sr))
                            native_ch = max(1, min(2, int(d.get('max_input_channels', native_ch) or native_ch)))
                        else:
                            # Ruta 3 (fallback robusto): soundcard loopback por altavoz
                            try:
                                import soundcard as sc

                                speaker = None
                                if pref_name:
                                    for sp in sc.all_speakers():
                                        if pref_name.lower() in sp.name.lower():
                                            speaker = sp
                                            break
                                if speaker is None:
                                    # intenta matching por nombre del output por defecto de sounddevice
                                    for sp in sc.all_speakers():
                                        if out_name.lower() in sp.name.lower() or sp.name.lower() in out_name.lower():
                                            speaker = sp
                                            break
                                if speaker is None:
                                    speaker = sc.default_speaker()

                                if speaker is None:
                                    print("[JARVIS] ⚠️ Loopback: sin speaker por defecto para fallback")
                                    return

                                print(f"[JARVIS] 🎵 Loopback fallback soundcard: {speaker.name}")
                                loop_mic = None
                                # API correcta de soundcard para loopback del speaker
                                try:
                                    loop_mic = sc.get_microphone(speaker.name, include_loopback=True)
                                except Exception:
                                    # fallback por id/str para algunas versiones
                                    try:
                                        loop_mic = sc.get_microphone(str(getattr(speaker, "id", speaker.name)), include_loopback=True)
                                    except Exception:
                                        loop_mic = None

                                if loop_mic is None:
                                    print("[JARVIS] ⚠️ Loopback: soundcard no pudo crear micrófono loopback")
                                    return

                                # En algunos drivers MediaFoundation emite discontinuities frecuentes;
                                # las ignoramos para no saturar el loop/terminal.
                                warnings.filterwarnings(
                                    "ignore",
                                    message="data discontinuity in recording",
                                    module="soundcard.mediafoundation",
                                )

                                with loop_mic.recorder(samplerate=native_sr, channels=1, blocksize=2048) as rec:
                                    print("[JARVIS] 🎵 Loopback captura activa")
                                    while not _lb_stop.is_set():
                                        frames = rec.record(numframes=2048)
                                        if frames is None:
                                            continue
                                        ch = frames[:, 0] if getattr(frames, 'ndim', 1) > 1 else frames
                                        flat = np.clip(ch * 32767.0, -32768, 32767).astype(np.int16)
                                        _publish_audio_samples(flat, native_sr)
                                return
                            except Exception as e_sc:
                                print(f"[JARVIS] ⚠️ Loopback: no se encontró dispositivo loopback ({e_sc})")
                                return

                    def _lb_cb(indata, frames, _t, _status):
                        if _lb_stop.is_set():
                            raise _sd.CallbackStop()
                        ch = indata[:, 0] if indata.ndim > 1 else indata.flatten()
                        # el driver puede devolver float32 (-1..1) o int16
                        if ch.dtype.kind == 'f':
                            flat = (ch * 32767.0).astype(np.int16)
                        else:
                            flat = ch.astype(np.int16)
                        _publish_audio_samples(flat.copy(), native_sr)

                    stream_kwargs = dict(
                        device=loop_dev,
                        samplerate=native_sr,
                        channels=native_ch,
                        dtype='float32',
                        blocksize=1024,
                        callback=_lb_cb,
                    )
                    if extra is not None:
                        stream_kwargs['extra_settings'] = extra

                    with _sd.InputStream(**stream_kwargs):
                        print("[JARVIS] 🎵 Loopback captura activa")
                        while not _lb_stop.is_set():
                            time.sleep(0.1)
                except Exception as e:
                    print(f"[JARVIS] ⚠️ Loopback: {e}")

            def _start_loopback():
                _lb_stop.clear()
                t = threading.Thread(target=_loopback_worker, daemon=True)
                _lb_thread_ref[0] = t
                t.start()

            def _stop_loopback():
                _lb_stop.set()
                _lb_thread_ref[0] = None

            def _handle_play_cmd(action, params):
                try:
                    # Llamada asíncrona al helper ytmusic / headless backend
                    def _call():
                        try:
                            p = {**(params or {})}
                            if _HEADLESS and hasattr(ymod, 'play'):
                                # map common actions to headless functions
                                a = action.lower()
                                if a == 'play':
                                    q = p.get('query') or p.get('q') or ''
                                    ymod.play(q)
                                elif a in ('pause', 'play_pause'):
                                    ymod.pause()
                                elif a in ('resume', 'play_resume'):
                                    ymod.resume()
                                elif a in ('toggle_play', 'toggle'):
                                    ymod.toggle_play()
                                elif a == 'stop':
                                    ymod.stop()
                                elif a == 'volume':
                                    lvl = p.get('level') or p.get('level') or p.get('volume')
                                    if lvl is not None:
                                        lvl_i = int(lvl)
                                        ymod.volume(lvl_i)
                                        try:
                                            self.ui.set_music_volume(lvl_i)
                                        except Exception:
                                            pass
                                elif a == 'seek':
                                    sec = p.get('seconds') or p.get('pos') or p.get('seek') or p.get('position')
                                    if sec is not None:
                                        ymod.seek(int(sec))
                                elif a in ('next', 'next_track'):
                                    ymod.next()
                                elif a in ('previous', 'prev', 'previous_track'):
                                    ymod.previous()
                                elif a == 'play_playlist':
                                    q = (
                                        p.get('query')
                                        or p.get('playlist')
                                        or p.get('playlist_id')
                                        or p.get('query_or_id')
                                        or ''
                                    )
                                    lim = int(p['limit']) if p.get('limit') not in (None, '', 0, '0') else None
                                    shf = p.get('shuffle', False)
                                    if not isinstance(shf, bool):
                                        shf = str(shf).strip().lower() in ('1', 'true', 'yes', 'y', 'on', 'si', 'sí')
                                    start = int(p.get('start_index', 0) or 0)
                                    ymod.play_playlist(q, lim, shf, start)
                                elif a == 'play_tracks':
                                    tracks = p.get('tracks') or []
                                    start = int(p.get('start_index', 0) or 0)
                                    shf = p.get('shuffle', False)
                                    if not isinstance(shf, bool):
                                        shf = str(shf).strip().lower() in ('1', 'true', 'yes', 'y', 'on', 'si', 'sí')
                                    if hasattr(ymod, 'play_tracks'):
                                        ymod.play_tracks(tracks, start, shf)
                                elif a == 'play_track':
                                    if hasattr(ymod, 'play_track'):
                                        ymod.play_track(
                                            p.get('videoId') or p.get('video_id') or '',
                                            p.get('title') or '',
                                            p.get('artists') or '',
                                        )
                                elif a == 'prefetch_tracks':
                                    if hasattr(ymod, 'prefetch_tracks'):
                                        ymod.prefetch_tracks(
                                            p.get('tracks') or [],
                                            int(p.get('start_index', 0) or 0),
                                            int(p.get('count', 4) or 4),
                                        )
                                elif a == 'warmup':
                                    if hasattr(ymod, 'warmup'):
                                        ymod.warmup()
                                elif a == 'set_like':
                                    video_id = str(p.get('video_id') or p.get('videoId') or '').strip()
                                    liked = p.get('liked', True)
                                    if not isinstance(liked, bool):
                                        liked = str(liked).strip().lower() in ('1', 'true', 'yes', 'on', 'si', 'sí')
                                    error = ""
                                    try:
                                        from actions.ytmusic import set_song_like
                                        set_song_like(video_id, liked)
                                    except Exception as exc:
                                        error = str(exc)
                                    ui.set_playback_like_state(video_id, liked, error)
                                else:
                                    # unknown action: try generic ymod method
                                    if hasattr(ymod, a):
                                        getattr(ymod, a)(**p)
                            else:
                                # GUI backend: call existing `ytmusic` function
                                params_obj = {'action': action}
                                params_obj.update(p)
                                jarvis_inst = globals().get('JARVIS_INSTANCE')
                                speak_fn = getattr(jarvis_inst, 'speak', None) if jarvis_inst else None
                                try:
                                    if speak_fn:
                                        ytmusic(params_obj, player=None, speak=speak_fn)
                                    else:
                                        ytmusic(params_obj, player=None, speak=lambda *a, **k: None)
                                except Exception:
                                    # best effort
                                    pass
                        except Exception:
                            pass
                    threading.Thread(target=_call, daemon=True).start()
                except Exception:
                    pass

            ui.on_playback_command = _handle_play_cmd

            # Poller para refrescar info de la pista actual y actualizar la UI
            _was_playing = [False]
            _last_like_video = [""]
            _last_like_state = [None]

            def _load_like_state(video_id: str):
                liked = False
                try:
                    from actions.ytmusic import get_song_like_status
                    liked = get_song_like_status(video_id)
                except Exception:
                    pass
                if video_id == _last_like_video[0]:
                    _last_like_state[0] = liked
                    ui.set_playback_like_state(video_id, liked)

            def _poller():
                while True:
                    try:
                        playing = False
                        # Headless backend exposes `current()`
                        if _HEADLESS and hasattr(ymod, 'current'):
                            info = ymod.current()
                            t_  = info.get('title', '') if info else ''
                            a_  = info.get('artists', '') if info else ''
                            pos = float(info.get('position', 0) or 0) if info else 0.0
                            dur = float(info.get('duration', 0) or 0) if info else 0.0
                            playing = bool(info.get('playing', False)) if info else False
                            video_id = str(info.get('videoId') or '') if info else ''
                            if video_id and video_id != _last_like_video[0]:
                                _last_like_video[0] = video_id
                                _last_like_state[0] = None
                                threading.Thread(
                                    target=_load_like_state,
                                    args=(video_id,),
                                    daemon=True,
                                ).start()
                            ui.update_playback(
                                t_,
                                a_,
                                pos,
                                dur,
                                playing,
                                video_id,
                                _last_like_state[0],
                            )
                        else:
                            ui.update_playback('', '', 0, 0, False)

                        # activar/desactivar loopback según estado de reproducción
                        ui.set_music_playing(playing)
                        if playing and not _was_playing[0]:
                            _start_loopback()
                        elif not playing and _was_playing[0]:
                            _stop_loopback()
                            ui.set_fft_bins([0.0] * 64)
                        _was_playing[0] = playing
                    except Exception:
                        pass
                    time.sleep(1)

            t = threading.Thread(target=_poller, daemon=True)
            t.start()
        except Exception:
            pass

    # Instalar handlers antes de arrancar el loop de UI
    _install_playback_handlers()

    threading.Thread(target=runner, daemon=True).start()
    try:
        ui.root.mainloop()
    finally:
        manager = getattr(ui, "whatsapp_manager", None)
        if manager is not None:
            manager.stop()
        stop_bridge()

if __name__ == "__main__":
    main()
