"""Small LAN-only web dashboard mirroring the app's log/toast/error stream,
paired from a phone via a QR code. Opt-in (lan_dashboard_enabled=False by
default) because it binds 0.0.0.0 — reachable by anything on the same
network, gated only by a token embedded in the QR URL.

Token is generated once and persisted via app_settings so a scanned QR
keeps working across restarts (user-chosen trade-off: convenience over
per-session token rotation).

No HTTPS by default. The server always binds 0.0.0.0, so reaching it from
outside the LAN is purely a router/firewall matter (port-forward the
dashboard port to this machine) — this module never touches either. When
"lan_dashboard_public_mode" is on, the QR/pairing URL embeds the router's
public IP instead of the LAN IP; the token is still the only gate, same as
the LAN-only URL, so anyone who has it can reach the API from the internet.
"""
from __future__ import annotations

import collections
import secrets
import socket
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeout

from actions import app_settings
from actions import event_bus

_buffer_lock = threading.Lock()
_log_buffer: "collections.deque[dict]" = collections.deque(maxlen=500)
_next_id = 0

_app = None
_started = False
_ui_ref = None
_jarvis_ref = None

_MUSIC_ACTIONS = {
    "play", "pause", "toggle", "next", "previous", "volume", "seek",
    "play_playlist", "play_tracks", "play_track", "jump_to", "set_like",
    # System media controls (lock screen / notification) send discrete PLAY and
    # PAUSE commands rather than a toggle, and "play" here means "search and
    # play this query" — so resuming needs its own action to be unambiguous.
    "resume",
}
# NOT whitelisted on purpose: "prefetch_tracks". Every action here is executed
# on the single serialized playback worker, so queueing prefetch warm-ups
# there delays the user's real transport commands behind them (~1s each).
# /api/music/prefetch handles warm-ups instead, off that worker entirely.

# How long a transport request may hold the HTTP connection before it answers
# "accepted, still working". Resolving a cold stream takes a couple of seconds;
# blocking the phone for all of it (and erroring out past the old 5 s timeout,
# which the app swallowed silently) made a tap look like it did nothing, so the
# user tapped again and queued a second play behind the first.
_MUSIC_ACTION_WAIT_SECONDS = 2.0
# Streams warmed per prefetch request from the phone.
_MAX_PHONE_PREFETCH = 3


# The only app settings the phone may flip. Kept explicit so the automations
# endpoint can never be used to write arbitrary keys over the network.
_WA_SETTING_KEYS = ("whatsapp_auto_translate", "whatsapp_auto_transcribe")

# Desktop controls reachable from the phone. A name whitelist, NOT a getattr
# over the whole module: computer_settings also exposes things like
# type_text(), and an open dispatch would hand the network a keyboard.
_REMOTE_ACTIONS = frozenset({
    "volume_up", "volume_down", "volume_mute", "volume_set",
    "lock_screen", "sleep_display", "show_desktop", "open_system_settings",
    "restart_computer", "shutdown_computer",
    "app_launch",
})

# Incremented every time the desktop asks the phone to show the controller
# prompt again. The phone compares it against the last value it saw, so a
# re-announce reaches it even if it had dismissed the prompt.
_gamepad_announce = 0


# Cover sizes the phone actually needs, in physical pixels (logical size ×3 for
# a dense screen). YouTube Music serves list artwork at 120², which is mush on
# a 260pt now-playing cover — but pulling 800² for a 44pt row would just waste
# the phone's data, so each surface asks for its own size.
_COVER_NOW_PLAYING = 800
_COVER_GRID = 544
_COVER_ROW = 240


# Dominant cover colours, keyed by artwork URL. Downloading and quantising a
# cover takes a moment, and the phone polls status every few seconds.
_cover_color_cache: "collections.OrderedDict[str, str]" = collections.OrderedDict()
_COVER_COLOR_CACHE_MAX = 128


_cover_color_lock = threading.Lock()
_cover_color_inflight: set[str] = set()


def cover_accent_color(url: str) -> str:
    """Cached dominant colour for a cover, computed off the request thread.

    Returns "" the first time a cover is seen and schedules the work: reading
    one costs over a second, and this is called from /api/status, which the
    phone polls every few seconds — blocking there would stall the whole UI.
    The next poll picks up the cached value.
    """
    url = str(url or "").strip()
    if not url:
        return ""
    with _cover_color_lock:
        cached = _cover_color_cache.get(url)
        if cached is not None:
            _cover_color_cache.move_to_end(url)
            return cached
        if url in _cover_color_inflight:
            return ""
        _cover_color_inflight.add(url)

    threading.Thread(target=_compute_cover_color, args=(url,), daemon=True).start()
    return ""


def _compute_cover_color(url: str) -> str:
    """Quantise the cover and take the most-used bucket that still has some
    saturation — an average would come back grey, and the biggest bucket alone
    is usually the artwork's black border."""
    color = ""
    try:
        import colorsys
        import io as _io
        import requests
        from PIL import Image

        raw = requests.get(url, timeout=6).content
        image = Image.open(_io.BytesIO(raw)).convert("RGB")
        image.thumbnail((96, 96))
        quantised = image.quantize(colors=16, method=Image.Quantize.FASTOCTREE)
        palette = quantised.getpalette() or []
        counts = sorted(quantised.getcolors() or [], reverse=True)

        best = None
        for count, index in counts:
            r, g, b = palette[index * 3:index * 3 + 3]
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            # Skip near-black/near-white and greys: they carry no accent.
            if 0.12 < l < 0.88 and s > 0.25:
                best = (r, g, b)
                break
            if best is None and 0.08 < l < 0.95:
                best = (r, g, b)  # fallback: least-bad neutral
        if best:
            color = "#{:02x}{:02x}{:02x}".format(*best)
    except Exception as e:
        print(f"[LanDashboard] cover colour failed: {e}")
        color = ""

    with _cover_color_lock:
        _cover_color_cache[url] = color
        _cover_color_inflight.discard(url)
        if len(_cover_color_cache) > _COVER_COLOR_CACHE_MAX:
            _cover_color_cache.popitem(last=False)
    return color


def _with_cover(items, size: int, key: str = "thumbnail"):
    """Rewrite each item's cover URL to the resolution `size`."""
    try:
        from actions.ytmusic import upgrade_thumbnail_url
    except Exception:
        return items
    for item in items or []:
        if isinstance(item, dict) and item.get(key):
            item[key] = upgrade_thumbnail_url(str(item[key]), size)
    return items


def bump_gamepad_announce() -> int:
    """Called from the desktop UI's 'Mando móvil' button."""
    global _gamepad_announce
    _gamepad_announce += 1
    return _gamepad_announce


# libretro's controller-1 button names (actions/libretro.py JOYPAD). Validated
# here so a typo from the phone is dropped instead of reaching the core.
_GAMEPAD_BUTTONS = frozenset({
    "a", "b", "x", "y", "up", "down", "left", "right",
    "start", "select", "l", "r", "l2", "r2", "l3", "r3",
})


def set_ui(ui) -> None:
    """Stores the live JarvisUI reference so /api/status and /api/music/<action>
    can read playback/mode/mic state and drive playback controls. Safe to call
    before the dashboard is started — it's just stashed for later.

    Also wires a direct callback for conversation/log lines. NOT done via
    actions.event_bus.log() — ActionEvent.LOG already has a subscriber (in
    ui/__init__.py) that calls write_log() in response to a LOG event;
    emitting one from inside write_log() would create an infinite feedback
    loop (see the comment on JarvisUI.write_log)."""
    global _ui_ref
    _ui_ref = ui
    if ui is not None:
        ui._dashboard_log_cb = lambda text: _on_log_event({"source": "chat", "message": text})


def set_jarvis(jarvis) -> None:
    """Stores the live JarvisLive reference for /api/command. Passed in
    directly rather than looked up via `import main` — when the app runs as
    `python main.py`, that module is `__main__`, not `main`, so `import main`
    from here would load a second, empty copy instead of the running one."""
    global _jarvis_ref
    _jarvis_ref = jarvis


def get_lan_ip() -> str:
    """Best-effort LAN-facing IP (not the loopback/0.0.0.0 address)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# Tried in order; first one that answers wins. Multiple providers because any
# single one can be down/rate-limiting, and this only runs when the user
# explicitly opens the QR dialog — a couple hundred ms of fallback is fine.
_PUBLIC_IP_PROVIDERS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


def get_public_ip() -> str | None:
    """Router's WAN-facing IP, as seen from the internet. Returns None if
    every provider failed (no internet, all blocked by a firewall, etc.) —
    callers fall back to the last-known value or the LAN IP."""
    import requests
    for url in _PUBLIC_IP_PROVIDERS:
        try:
            resp = requests.get(url, timeout=4)
            resp.raise_for_status()
            ip = resp.text.strip()
            if ip.count(".") == 3:  # cheap IPv4 sanity check
                app_settings.set("lan_dashboard_last_public_ip", ip)
                return ip
        except Exception:
            continue
    return None


def _get_or_create_token() -> str:
    token = app_settings.get("lan_dashboard_token", "") or ""
    if token:
        return token
    token = secrets.token_urlsafe(16)
    app_settings.set("lan_dashboard_token", token)
    return token


def _append_entry(entry_type: str, source: str, message: str) -> None:
    """Appends to the buffer, coalescing consecutive duplicates. Some upstream
    log sources (e.g. the Gemini receive loop) can re-emit the exact same
    line back-to-back in a tight burst — without this, a single spoken
    reply could flood the phone feed with dozens of identical entries."""
    global _next_id
    with _buffer_lock:
        if _log_buffer and _log_buffer[-1]["type"] == entry_type \
                and _log_buffer[-1]["source"] == source \
                and _log_buffer[-1]["message"] == message:
            return
        _next_id += 1
        _log_buffer.append({
            "id": _next_id,
            "type": entry_type,
            "source": source,
            "message": message,
        })


def _on_log_event(data: dict) -> None:
    _append_entry("log", data.get("source", ""), data.get("message", ""))


def _on_toast_event(data: dict) -> None:
    _append_entry("toast", "", data.get("text", ""))


def _on_error_event(data: dict) -> None:
    _append_entry("error", data.get("source", ""), data.get("message", ""))


_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0A0C16">
<title>JARVIS</title>
<style>
:root {
  --bg:#0A0C16; --panel:#141829; --panel2:#1E2338; --border:#3A4160; --border-a:#252C48;
  --pri:#B6C4FF; --pri-dim:#5E82FF; --pri-gho:#131C46; --acc:#A7AFFF; --acc2:#8FA8FF;
  --green:#4ADE80; --red:#FF5E82; --text:#F2F3FA; --text-dim:#C9CDE4; --text-med:#9AA3C0;
  --white:#FFFFFF; --shadow:rgba(0,0,0,0.35);
  --safe-b: env(safe-area-inset-bottom, 0px);
  --safe-t: env(safe-area-inset-top, 0px);
}
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html,body { height:100%; }
body {
  background:var(--bg); color:var(--text); margin:0;
  font-family:-apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI", sans-serif;
  display:flex; flex-direction:column; overflow:hidden;
}
button { font-family:inherit; cursor:pointer; -webkit-user-select:none; user-select:none; }
button, .mbtn, .navbtn { transition:transform .12s ease, background .15s ease, border-color .15s ease, color .15s ease; }
button:active, .mbtn:active, .navbtn:active { transform:scale(0.94); }
@keyframes fadeUp { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }
@keyframes fadeIn { from { opacity:0; } to { opacity:1; } }

/* ---------- header ---------- */
header {
  flex:none; padding:calc(14px + var(--safe-t)) 18px 12px;
  background:linear-gradient(180deg, rgba(19,28,70,0.55), rgba(10,12,22,0));
  border-bottom:1px solid var(--border-a);
}
.hdr-row { display:flex; align-items:center; gap:10px; }
.hdr-title { font-size:16px; font-weight:800; letter-spacing:0.07em; color:var(--pri); flex:1; }
.dot { width:8px; height:8px; border-radius:50%; background:var(--text-med); flex:none; transition:background .3s ease; }
.dot.live { background:var(--green); box-shadow:0 0 0 0 rgba(74,222,128,0.6); animation:pulse 2s infinite; }
@keyframes pulse {
  0%   { box-shadow:0 0 0 0 rgba(74,222,128,0.45); }
  70%  { box-shadow:0 0 0 6px rgba(74,222,128,0); }
  100% { box-shadow:0 0 0 0 rgba(74,222,128,0); }
}
.pills { display:flex; gap:6px; margin-top:10px; flex-wrap:wrap; }
.pill {
  font-size:11px; font-weight:600; color:var(--text-med); background:var(--panel);
  border:1px solid var(--border-a); border-radius:20px; padding:5px 11px;
  display:flex; align-items:center; gap:5px; transition:color .2s ease, border-color .2s ease;
}
.pill b { color:var(--text-dim); font-weight:700; }
.pill.warn { color:var(--red); border-color:rgba(255,94,130,0.35); }
.pill.warn b { color:var(--red); }

/* ---------- screens ---------- */
.screen { flex:1; min-height:0; display:none; flex-direction:column; animation:fadeIn .18s ease; }
.screen.active { display:flex; }

/* ---------- mini player (Chat screen) ---------- */
#player {
  flex:none; margin:12px 16px 0; background:var(--panel);
  border:1px solid var(--border-a); border-radius:18px; padding:11px 12px;
  display:flex; align-items:center; gap:10px; box-shadow:0 6px 16px var(--shadow);
}
#player.empty { opacity:0.7; }
#player .meta { flex:1; min-width:0; cursor:pointer; }
#player .title { font-size:13px; font-weight:700; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
#player .artist { font-size:11px; color:var(--text-med); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:1px; }
#player.empty .title { color:var(--text-med); font-weight:600; }
.mbtn {
  width:38px; height:38px; border-radius:50%; flex:none;
  background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.09);
  display:flex; align-items:center; justify-content:center; color:var(--pri);
}
.mbtn:active { background:rgba(182,196,255,0.18); border-color:rgba(182,196,255,0.3); }
.mbtn svg { width:16px; height:16px; }
.mbtn.play { width:44px; height:44px; background:var(--pri-gho); border-color:rgba(182,196,255,0.35); }
.mbtn.play svg { width:18px; height:18px; }

/* ---------- feed (Chat screen) ---------- */
#feed-wrap { flex:1; overflow-y:auto; padding:16px 16px 10px; -webkit-overflow-scrolling:touch; }
#feed { display:flex; flex-direction:column; gap:9px; }
.msg { max-width:86%; padding:10px 14px; border-radius:17px; font-size:14.5px; line-height:1.4; word-wrap:break-word; animation:fadeUp .22s ease; box-shadow:0 3px 10px var(--shadow); }
.msg .meta { font-size:10px; opacity:0.65; margin-bottom:3px; letter-spacing:0.04em; text-transform:uppercase; font-weight:700; }
.msg.you { align-self:flex-end; background:var(--pri-dim); color:#08101F; border-bottom-right-radius:4px; }
.msg.you .meta { color:rgba(8,16,31,0.65); }
.msg.jarvis { align-self:flex-start; background:var(--panel2); color:var(--text); border:1px solid var(--border-a); border-bottom-left-radius:4px; }
.msg.jarvis .meta { color:var(--pri); }
.msg.sys { align-self:center; background:transparent; color:var(--text-med); font-size:12px; padding:4px 10px; max-width:100%; text-align:center; box-shadow:none; }
.msg.error { align-self:center; background:rgba(255,94,130,0.10); color:var(--red); border:1px solid rgba(255,94,130,0.3); font-size:12px; max-width:95%; box-shadow:none; }
#empty-hint { text-align:center; color:var(--text-med); font-size:12.5px; padding:44px 24px; line-height:1.5; }

/* ---------- composer (Chat screen) ---------- */
#composer {
  flex:none; display:flex; align-items:flex-end; gap:8px;
  padding:10px 12px calc(10px + var(--safe-b));
  background:linear-gradient(0deg, rgba(10,12,22,0.92), rgba(10,12,22,0.6));
  border-top:1px solid var(--border-a);
}
#cmd-input {
  flex:1; background:var(--panel); color:var(--text); border:1px solid var(--border);
  border-radius:22px; padding:12px 17px; font-size:15px; resize:none; max-height:100px;
  font-family:inherit; outline:none; transition:border-color .15s ease;
}
#cmd-input:focus { border-color:rgba(182,196,255,0.55); }
#send-btn {
  width:46px; height:46px; border-radius:50%; flex:none; background:var(--pri-dim);
  border:none; color:#08101F; display:flex; align-items:center; justify-content:center;
  box-shadow:0 4px 12px rgba(94,130,255,0.35);
}
#send-btn:active { background:var(--pri); }
#send-btn:disabled { opacity:0.4; box-shadow:none; }
#send-btn svg { width:18px; height:18px; }

/* ---------- Música screen ---------- */
#screen-music { align-items:center; justify-content:flex-start; padding:24px 24px 12px; overflow-y:auto; }
.eq { display:flex; align-items:flex-end; justify-content:center; gap:5px; height:64px; margin:8px 0 22px; }
.eq span { width:6px; border-radius:3px; background:linear-gradient(180deg, var(--pri), var(--pri-dim)); height:14px; opacity:0.35; transition:opacity .3s ease; }
.eq.playing span { opacity:1; animation:eqbar 1.1s ease-in-out infinite; }
.eq span:nth-child(1){ animation-delay:-0.9s; } .eq span:nth-child(2){ animation-delay:-0.6s; }
.eq span:nth-child(3){ animation-delay:-1.1s; } .eq span:nth-child(4){ animation-delay:-0.3s; }
.eq span:nth-child(5){ animation-delay:-0.7s; }
@keyframes eqbar { 0%,100% { height:14px; } 50% { height:56px; } }
.mtitle { font-size:22px; font-weight:800; color:var(--text); text-align:center; line-height:1.3; padding:0 8px; }
.martist { font-size:14px; color:var(--text-med); text-align:center; margin-top:4px; }
#music-progress { width:100%; max-width:340px; margin-top:28px; }
.prg-bar { width:100%; height:5px; border-radius:3px; background:var(--panel2); overflow:hidden; }
.prg-fill { height:100%; width:0%; background:linear-gradient(90deg, var(--pri-dim), var(--pri)); border-radius:3px; transition:width .25s linear; }
.prg-times { display:flex; justify-content:space-between; font-size:11px; color:var(--text-med); margin-top:6px; font-variant-numeric:tabular-nums; }
#music-controls { display:flex; align-items:center; justify-content:center; gap:22px; margin-top:30px; }
#music-controls .mbtn { width:52px; height:52px; }
#music-controls .mbtn svg { width:22px; height:22px; }
#music-controls .mbtn.play { width:68px; height:68px; }
#music-controls .mbtn.play svg { width:28px; height:28px; }
#music-volume { width:100%; max-width:340px; margin-top:34px; display:flex; align-items:center; gap:10px; }
#music-volume svg { width:18px; height:18px; color:var(--text-med); flex:none; }
input[type=range] {
  -webkit-appearance:none; appearance:none; flex:1; height:4px; border-radius:2px;
  background:var(--panel2); outline:none;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance:none; width:18px; height:18px; border-radius:50%;
  background:var(--pri); border:3px solid #08101F; box-shadow:0 2px 6px var(--shadow); cursor:pointer;
}
#music-empty { text-align:center; color:var(--text-med); font-size:13px; margin-top:60px; padding:0 20px; line-height:1.6; }

/* ---------- Música: sub-nav + playlists/search/queue ---------- */
.subnav { flex:none; display:flex; gap:6px; padding:12px 16px 4px; }
.subtab {
  flex:1; background:var(--panel); color:var(--text-med); border:1px solid var(--border-a);
  border-radius:12px; padding:9px 8px; font-size:12.5px; font-weight:700;
}
.subtab.active { background:var(--pri-gho); color:var(--pri); border-color:rgba(182,196,255,0.4); }
.mtab { flex:1; min-height:0; overflow-y:auto; padding:8px 18px 20px; }
#screen-music { padding:0; align-items:stretch; }
#mtab-now { display:flex; flex-direction:column; align-items:center; padding-top:16px; }
h2.sec { font-size:11px; letter-spacing:0.06em; text-transform:uppercase; color:var(--acc2); font-weight:800; margin:26px 0 10px; width:100%; max-width:340px; }
#queue-list { width:100%; max-width:340px; display:flex; flex-direction:column; gap:6px; }
.q-row { display:flex; align-items:center; gap:10px; background:var(--panel); border:1px solid var(--border-a); border-radius:12px; padding:9px 12px; animation:fadeUp .2s ease; }
.q-row .qi { font-size:11px; color:var(--text-med); width:16px; flex:none; text-align:center; }
.q-row .qm { min-width:0; flex:1; }
.q-row .qt { font-size:13px; font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.q-row .qa { font-size:11px; color:var(--text-med); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.q-row.current { border-color:rgba(182,196,255,0.4); background:var(--pri-gho); }
.q-row.current .qi { color:var(--pri); }
#queue-empty, #pl-empty, #search-empty { text-align:center; color:var(--text-med); font-size:12.5px; padding:24px 10px; }

.pl-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.pl-card { background:var(--panel); border:1px solid var(--border-a); border-radius:14px; padding:14px 12px; text-align:left; box-shadow:0 3px 10px var(--shadow); animation:fadeUp .2s ease; }
.pl-card .plt { font-size:13px; font-weight:700; color:var(--text); line-height:1.3; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.pl-card .pls { font-size:11px; color:var(--text-med); margin-top:5px; }

#search-box { display:flex; gap:8px; margin-bottom:14px; position:sticky; top:0; background:var(--bg); padding-top:4px; padding-bottom:4px; z-index:1; }
#search-input {
  flex:1; background:var(--panel); color:var(--text); border:1px solid var(--border);
  border-radius:14px; padding:11px 15px; font-size:14.5px; font-family:inherit; outline:none;
}
#search-input:focus { border-color:rgba(182,196,255,0.55); }
.sr-row { display:flex; align-items:center; gap:10px; background:var(--panel); border:1px solid var(--border-a); border-radius:12px; padding:10px 12px; margin-bottom:7px; animation:fadeUp .2s ease; }
.sr-row .srm { min-width:0; flex:1; }
.sr-row .srt { font-size:13.5px; font-weight:700; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sr-row .sra { font-size:11.5px; color:var(--text-med); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sr-play { width:34px; height:34px; border-radius:50%; flex:none; background:var(--pri-gho); border:1px solid rgba(182,196,255,0.35); color:var(--pri); display:flex; align-items:center; justify-content:center; }
.sr-play svg { width:15px; height:15px; }

/* ---------- WhatsApp screen ---------- */
#screen-whatsapp { padding:0; }
#wa-list-view, #wa-thread-view { flex:1; min-height:0; display:flex; flex-direction:column; }
#wa-chats-wrap { flex:1; overflow-y:auto; padding:12px 16px 16px; }
.wa-row { display:flex; align-items:center; gap:12px; padding:11px 8px; border-radius:14px; }
.wa-row:active { background:rgba(255,255,255,0.04); }
.wa-avatar {
  width:44px; height:44px; border-radius:50%; flex:none; background:var(--panel2);
  display:flex; align-items:center; justify-content:center; font-weight:800; color:var(--pri); font-size:16px;
}
.wa-meta { min-width:0; flex:1; }
.wa-top { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.wa-name { font-size:14px; font-weight:700; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.wa-time { font-size:10.5px; color:var(--text-med); flex:none; }
.wa-preview { font-size:12.5px; color:var(--text-med); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:2px; }
.wa-badge {
  background:var(--green); color:#08101F; font-size:10.5px; font-weight:800; border-radius:10px;
  padding:1px 7px; flex:none; margin-left:6px;
}
#wa-thread-header {
  flex:none; display:flex; align-items:center; gap:10px; padding:12px 16px;
  border-bottom:1px solid var(--border-a); background:var(--panel);
}
#wa-back { width:34px; height:34px; border-radius:50%; flex:none; background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.09); color:var(--pri); display:flex; align-items:center; justify-content:center; }
#wa-back svg { width:16px; height:16px; }
#wa-thread-name { font-size:14.5px; font-weight:700; color:var(--text); }
#wa-messages-wrap { flex:1; overflow-y:auto; padding:14px 16px; }
#wa-messages { display:flex; flex-direction:column; gap:8px; }
.wa-composer {
  flex:none; display:flex; align-items:flex-end; gap:8px; padding:10px 12px calc(10px + var(--safe-b));
  background:linear-gradient(0deg, rgba(10,12,22,0.92), rgba(10,12,22,0.6)); border-top:1px solid var(--border-a);
}
#wa-input {
  flex:1; background:var(--panel); color:var(--text); border:1px solid var(--border);
  border-radius:22px; padding:12px 17px; font-size:15px; resize:none; max-height:100px;
  font-family:inherit; outline:none; transition:border-color .15s ease;
}
#wa-input:focus { border-color:rgba(182,196,255,0.55); }
#wa-send-btn {
  width:46px; height:46px; border-radius:50%; flex:none; background:var(--pri-dim);
  border:none; color:#08101F; display:flex; align-items:center; justify-content:center;
  box-shadow:0 4px 12px rgba(94,130,255,0.35);
}
#wa-send-btn:active { background:var(--pri); }
#wa-send-btn:disabled { opacity:0.4; box-shadow:none; }
#wa-send-btn svg { width:18px; height:18px; }

/* ---------- Ajustes screen ---------- */
#screen-settings { padding:20px 18px; overflow-y:auto; gap:14px; }
.card { background:var(--panel); border:1px solid var(--border-a); border-radius:16px; padding:16px; box-shadow:0 4px 14px var(--shadow); }
.card h3 { margin:0 0 10px; font-size:11px; letter-spacing:0.06em; text-transform:uppercase; color:var(--acc2); font-weight:800; }
.kv { display:flex; justify-content:space-between; gap:12px; padding:7px 0; font-size:13px; border-top:1px solid var(--border-a); }
.kv:first-of-type { border-top:none; }
.kv .k { color:var(--text-med); }
.kv .v { color:var(--text); font-weight:600; text-align:right; word-break:break-all; }
.card p { font-size:12.5px; color:var(--text-med); line-height:1.55; margin:0; }
.copy-btn {
  width:100%; margin-top:12px; background:var(--pri-gho); color:var(--pri); border:1px solid rgba(182,196,255,0.35);
  border-radius:12px; padding:11px; font-size:13.5px; font-weight:700;
}
.copy-btn:active { background:rgba(94,130,255,0.25); }
.copy-btn.copied { background:rgba(74,222,128,0.15); color:var(--green); border-color:rgba(74,222,128,0.4); }

/* ---------- bottom nav ---------- */
#navbar {
  flex:none; display:flex; padding:6px 8px calc(6px + var(--safe-b));
  background:rgba(13,16,32,0.96); border-top:1px solid var(--border-a);
}
.navbtn {
  flex:1; display:flex; flex-direction:column; align-items:center; gap:3px;
  background:none; border:none; color:var(--text-med); padding:7px 4px 5px; border-radius:12px;
}
.navbtn svg { width:22px; height:22px; }
.navbtn span { font-size:10px; font-weight:700; letter-spacing:0.02em; }
.navbtn.active { color:var(--pri); }
.navbtn.active svg { filter:drop-shadow(0 0 6px rgba(182,196,255,0.5)); }
</style></head>
<body>

<header>
  <div class="hdr-row">
    <span class="dot" id="live-dot"></span>
    <span class="hdr-title">JARVIS</span>
  </div>
  <div class="pills">
    <span class="pill" id="pill-mode">Modo: <b id="pill-mode-v">—</b></span>
    <span class="pill" id="pill-mic">Mic: <b id="pill-mic-v">—</b></span>
  </div>
</header>

<!-- ===== Chat screen ===== -->
<div class="screen active" id="screen-chat">
  <div id="player" class="empty">
    <button class="mbtn" onclick="musicAction('previous')" aria-label="Anterior">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
    </button>
    <div class="meta" onclick="switchScreen('music')">
      <div class="title" id="pl-title">Nada sonando</div>
      <div class="artist" id="pl-artist">—</div>
    </div>
    <button class="mbtn play" id="pl-toggle" onclick="musicAction('toggle')" aria-label="Play/Pause">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
    </button>
    <button class="mbtn" onclick="musicAction('next')" aria-label="Siguiente">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 6h2v12h-2zm-2 6L5.5 6v12z"/></svg>
    </button>
  </div>

  <div id="feed-wrap">
    <div id="empty-hint">Sin actividad todavía.<br>Envía una orden abajo.</div>
    <div id="feed"></div>
  </div>

  <div id="composer">
    <textarea id="cmd-input" rows="1" placeholder="Escribe una orden o pregunta…"></textarea>
    <button id="send-btn" onclick="sendCommand()" aria-label="Enviar">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
    </button>
  </div>
</div>

<!-- ===== Música screen ===== -->
<div class="screen" id="screen-music">
  <div class="subnav">
    <button class="subtab active" data-mtab="now" onclick="switchMusicTab('now')">Reproduciendo</button>
    <button class="subtab" data-mtab="playlists" onclick="switchMusicTab('playlists')">Playlists</button>
    <button class="subtab" data-mtab="search" onclick="switchMusicTab('search')">Buscar</button>
  </div>

  <div class="mtab" id="mtab-now">
    <div id="music-content" style="display:none; width:100%; flex-direction:column; align-items:center;">
      <div class="eq" id="music-eq">
        <span></span><span></span><span></span><span></span><span></span>
      </div>
      <div class="mtitle" id="m-title">—</div>
      <div class="martist" id="m-artist">—</div>

      <div id="music-progress">
        <div class="prg-bar"><div class="prg-fill" id="prg-fill"></div></div>
        <div class="prg-times"><span id="prg-elapsed">0:00</span><span id="prg-total">0:00</span></div>
      </div>

      <div id="music-controls">
        <button class="mbtn" onclick="musicAction('previous')" aria-label="Anterior">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>
        </button>
        <button class="mbtn play" id="m-toggle" onclick="musicAction('toggle')" aria-label="Play/Pause">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="mbtn" onclick="musicAction('next')" aria-label="Siguiente">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 6h2v12h-2zm-2 6L5.5 6v12z"/></svg>
        </button>
      </div>

      <div id="music-volume">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 00-2.5-4.03v8.06A4.5 4.5 0 0016.5 12z"/></svg>
        <input type="range" id="vol-slider" min="0" max="100" value="100">
      </div>
    </div>
    <div id="music-empty">No hay nada reproduciéndose ahora mismo.<br>Pide a JARVIS que ponga algo desde el chat.</div>

    <h2 class="sec">A continuación</h2>
    <div id="queue-list"></div>
    <div id="queue-empty" style="display:none;">La cola está vacía.</div>
  </div>

  <div class="mtab" id="mtab-playlists" style="display:none;">
    <div class="pl-grid" id="playlists-grid"></div>
    <div id="pl-empty" style="display:none;">No se encontraron playlists.</div>
  </div>

  <div class="mtab" id="mtab-search" style="display:none;">
    <div id="search-box">
      <input id="search-input" placeholder="Buscar canciones o artistas…" autocomplete="off">
    </div>
    <div id="search-results"></div>
    <div id="search-empty" style="display:none;">Escribe algo para buscar.</div>
  </div>
</div>

<!-- ===== WhatsApp screen ===== -->
<div class="screen" id="screen-whatsapp">
  <div id="wa-list-view">
    <div id="wa-chats-wrap">
      <div id="wa-chats"></div>
      <div id="wa-chats-empty" style="display:none;"></div>
    </div>
  </div>
  <div id="wa-thread-view" style="display:none;">
    <div id="wa-thread-header">
      <button id="wa-back" onclick="waBack()" aria-label="Volver">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M15 4l-8 8 8 8 1.4-1.4L9.8 12l6.6-6.6z"/></svg>
      </button>
      <span id="wa-thread-name">—</span>
    </div>
    <div id="wa-messages-wrap">
      <div id="wa-messages"></div>
    </div>
    <div class="wa-composer">
      <textarea id="wa-input" rows="1" placeholder="Escribe una respuesta…"></textarea>
      <button id="wa-send-btn" onclick="waSend()" aria-label="Enviar">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
      </button>
    </div>
  </div>
</div>

<!-- ===== Ajustes screen ===== -->
<div class="screen" id="screen-settings">
  <div class="card">
    <h3>Conexión</h3>
    <div class="kv"><span class="k">Host</span><span class="v" id="st-host">—</span></div>
    <div class="kv"><span class="k">Estado</span><span class="v" id="st-conn">—</span></div>
    <button class="copy-btn" id="copy-url-btn" onclick="copyUrl()">Copiar enlace del panel</button>
  </div>
  <div class="card">
    <h3>Acerca de</h3>
    <p>Panel remoto solo-LAN de JARVIS. Funciona únicamente dentro de tu red WiFi
    y mientras el token de la URL sea válido. No hay acceso desde fuera de casa.</p>
  </div>
</div>

<div id="navbar">
  <button class="navbtn active" data-screen="chat" onclick="switchScreen('chat')">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 4h16v12H7l-3 3V4z"/></svg>
    <span>Chat</span>
  </button>
  <button class="navbtn" data-screen="music" onclick="switchScreen('music')">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zm12-2a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
    <span>Música</span>
  </button>
  <button class="navbtn" data-screen="whatsapp" onclick="switchScreen('whatsapp')">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 00-8.6 15L2 22l5.2-1.4A10 10 0 1012 2zm0 18a8 8 0 01-4.1-1.1l-.3-.2-3 .8.8-2.9-.2-.3A8 8 0 1112 20zm4.4-6c-.2-.1-1.5-.7-1.7-.8-.2-.1-.4-.1-.6.1-.2.2-.7.8-.8 1-.2.2-.3.2-.5.1-.2-.1-1-.4-1.9-1.2-.7-.6-1.2-1.4-1.3-1.6-.1-.2 0-.4.1-.5l.4-.5c.1-.1.2-.3.2-.4.1-.2 0-.3 0-.4-.1-.1-.6-1.4-.8-1.9-.2-.5-.4-.4-.6-.4h-.5c-.2 0-.4.1-.6.3-.2.2-.8.8-.8 1.9s.8 2.2 1 2.4c.1.1 1.7 2.6 4.1 3.6.6.2 1 .4 1.4.5.6.2 1.1.1 1.5.1.5-.1 1.5-.6 1.7-1.2.2-.6.2-1.1.1-1.2-.1-.1-.2-.2-.4-.3z"/></svg>
    <span>WhatsApp</span>
  </button>
  <button class="navbtn" data-screen="settings" onclick="switchScreen('settings')">
    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 8a4 4 0 100 8 4 4 0 000-8zm8.4 4a7.4 7.4 0 01-.1 1.2l2.1 1.6-2 3.5-2.5-1a7.6 7.6 0 01-2.1 1.2l-.4 2.6h-4l-.4-2.6a7.6 7.6 0 01-2.1-1.2l-2.5 1-2-3.5 2.1-1.6a7.4 7.4 0 010-2.4L2.4 9.4l2-3.5 2.5 1a7.6 7.6 0 012.1-1.2L9.4 3h4l.4 2.7a7.6 7.6 0 012.1 1.2l2.5-1 2 3.5-2.1 1.6c.07.4.1.8.1 1.2z"/></svg>
    <span>Ajustes</span>
  </button>
</div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
let since = 0;
let sawAny = false;
let playing = false;
let musicSnapshot = { position:0, duration:0, playing:false, receivedAt:0 };
let userDraggingVolume = false;

let currentScreen = 'chat';

function switchScreen(name) {
  currentScreen = name;
  document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
  document.getElementById('screen-' + name).classList.add('active');
  document.querySelectorAll('.navbtn').forEach(el => el.classList.toggle('active', el.dataset.screen === name));
  if (name === 'music') loadQueue();
  if (name === 'whatsapp') loadChats();
}

let musicTab = 'now';
function switchMusicTab(name) {
  musicTab = name;
  document.querySelectorAll('.mtab').forEach(el => el.style.display = 'none');
  document.getElementById('mtab-' + name).style.display = (name === 'now') ? 'flex' : 'block';
  document.querySelectorAll('.subtab').forEach(el => el.classList.toggle('active', el.dataset.mtab === name));
  if (name === 'playlists') loadPlaylists();
}

function tagOf(item) {
  const msg = (item.message || '');
  if (item.type === 'error') return 'error';
  if (/^t[uú]:/i.test(msg)) return 'you';
  if (/^you:/i.test(msg)) return 'you';
  if (/^jarvis:/i.test(msg)) return 'jarvis';
  return 'sys';
}
function stripPrefix(msg) {
  return msg.replace(/^(t[uú]|you|jarvis|sys)\\s*:\\s*/i, '');
}
function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + ':' + String(s).padStart(2, '0');
}

async function pollLog() {
  try {
    const res = await fetch(`/api/log?since=${since}&token=${TOKEN}`);
    if (res.ok) {
      const items = await res.json();
      if (items.length) {
        const wrap = document.getElementById('feed-wrap');
        const feed = document.getElementById('feed');
        const atBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 60;
        for (const item of items) {
          since = Math.max(since, item.id);
          sawAny = true;
          const tag = tagOf(item);
          const div = document.createElement('div');
          div.className = 'msg ' + tag;
          const label = tag === 'you' ? 'Tú' : tag === 'jarvis' ? 'JARVIS' : (item.source || 'sistema');
          const showMeta = tag === 'you' || tag === 'jarvis';
          div.innerHTML = (showMeta ? `<div class="meta">${label}</div>` : '') +
            escapeHtml(stripPrefix(item.message || ''));
          feed.appendChild(div);
        }
        document.getElementById('empty-hint').style.display = sawAny ? 'none' : 'block';
        while (feed.children.length > 300) feed.removeChild(feed.firstChild);
        if (atBottom) wrap.scrollTop = wrap.scrollHeight;
      }
      setConn(true);
    } else {
      setConn(false);
    }
  } catch (e) {
    setConn(false);
  }
  setTimeout(pollLog, 2000);
}

function setConn(ok) {
  document.getElementById('live-dot').classList.toggle('live', ok);
  const el = document.getElementById('st-conn');
  if (el) el.textContent = ok ? 'Conectado' : 'Sin respuesta';
}

const PLAY_ICON = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const PAUSE_ICON = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';

async function pollStatus() {
  try {
    const res = await fetch(`/api/status?token=${TOKEN}`);
    if (res.ok) {
      const s = await res.json();
      const m = s.music || {};
      const player = document.getElementById('player');
      const hasTrack = !!m.title;

      if (hasTrack) {
        player.classList.remove('empty');
        document.getElementById('pl-title').textContent = m.title;
        document.getElementById('pl-artist').textContent = m.artists || '—';
      } else {
        player.classList.add('empty');
        document.getElementById('pl-title').textContent = 'Nada sonando';
        document.getElementById('pl-artist').textContent = '—';
      }

      playing = !!m.playing;
      const icon = playing ? PAUSE_ICON : PLAY_ICON;
      document.getElementById('pl-toggle').innerHTML = icon;
      document.getElementById('m-toggle').innerHTML = icon;
      document.getElementById('music-eq').classList.toggle('playing', playing);

      document.getElementById('music-content').style.display = hasTrack ? 'flex' : 'none';
      document.getElementById('music-empty').style.display = hasTrack ? 'none' : 'block';
      if (hasTrack) {
        document.getElementById('m-title').textContent = m.title;
        document.getElementById('m-artist').textContent = m.artists || '—';
        document.getElementById('prg-total').textContent = fmtTime(m.duration);
      }
      musicSnapshot = {
        position: m.position || 0, duration: m.duration || 0,
        playing, receivedAt: performance.now(),
      };

      if (!userDraggingVolume) {
        document.getElementById('vol-slider').value = m.volume != null ? m.volume : 100;
      }

      document.getElementById('pill-mode-v').textContent = s.mode || '—';
      const micEl = document.getElementById('pill-mic-v');
      const micPill = document.getElementById('pill-mic');
      micEl.textContent = s.muted ? 'silenciado' : 'activo';
      micPill.classList.toggle('warn', !!s.muted);
    }
  } catch (e) {}
  setTimeout(pollStatus, 2500);
}

function tickProgress() {
  const { position, duration, playing: p, receivedAt } = musicSnapshot;
  if (duration > 0) {
    const elapsed = p ? position + (performance.now() - receivedAt) / 1000 : position;
    const clamped = Math.min(elapsed, duration);
    document.getElementById('prg-fill').style.width = (clamped / duration * 100).toFixed(2) + '%';
    document.getElementById('prg-elapsed').textContent = fmtTime(clamped);
  }
  requestAnimationFrame(tickProgress);
}

function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

async function sendCommand() {
  const input = document.getElementById('cmd-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  autoGrow(input);
  const btn = document.getElementById('send-btn');
  btn.disabled = true;
  try {
    await fetch(`/api/command?token=${TOKEN}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text}),
    });
  } catch (e) {}
  btn.disabled = false;
  input.focus();
}

const inputEl = document.getElementById('cmd-input');
inputEl.addEventListener('input', () => autoGrow(inputEl));
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendCommand();
  }
});

async function musicAction(action, params) {
  try {
    await fetch(`/api/music/${action}?token=${TOKEN}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params || {}),
    });
  } catch (e) {}
}

function playTrack(t) {
  musicAction('play_track', { video_id: t.videoId, title: t.title, artists: t.artists || '' });
}
function playPlaylist(playlistId) {
  musicAction('play_playlist', { playlist_id: playlistId });
}

async function loadQueue() {
  try {
    const res = await fetch(`/api/music/queue?token=${TOKEN}`);
    if (!res.ok) return;
    const items = await res.json();
    const list = document.getElementById('queue-list');
    list.innerHTML = '';
    document.getElementById('queue-empty').style.display = items.length ? 'none' : 'block';
    items.forEach((t, i) => {
      const row = document.createElement('div');
      row.className = 'q-row' + (i === 0 ? ' current' : '');
      row.innerHTML = `<div class="qi">${i === 0 ? '▶' : (i + 1)}</div>
        <div class="qm"><div class="qt">${escapeHtml(t.title || '')}</div><div class="qa">${escapeHtml(t.artists || '')}</div></div>`;
      list.appendChild(row);
    });
  } catch (e) {}
}

async function loadPlaylists() {
  try {
    const res = await fetch(`/api/music/playlists?token=${TOKEN}`);
    if (!res.ok) return;
    const items = await res.json();
    const grid = document.getElementById('playlists-grid');
    grid.innerHTML = '';
    document.getElementById('pl-empty').style.display = items.length ? 'none' : 'block';
    items.forEach(p => {
      const card = document.createElement('button');
      card.className = 'pl-card';
      card.innerHTML = `<div class="plt">${escapeHtml(p.title || '')}</div>
        <div class="pls">${p.itemCount || 0} canciones</div>`;
      card.onclick = () => playPlaylist(p.playlistId);
      grid.appendChild(card);
    });
  } catch (e) {}
}

let searchDebounce = null;
document.getElementById('search-input').addEventListener('input', (e) => {
  clearTimeout(searchDebounce);
  const q = e.target.value.trim();
  if (!q) {
    document.getElementById('search-results').innerHTML = '';
    document.getElementById('search-empty').style.display = 'block';
    return;
  }
  searchDebounce = setTimeout(() => runSearch(q), 400);
});
async function runSearch(q) {
  try {
    const res = await fetch(`/api/music/search?q=${encodeURIComponent(q)}&token=${TOKEN}`);
    if (!res.ok) return;
    const items = await res.json();
    const list = document.getElementById('search-results');
    list.innerHTML = '';
    document.getElementById('search-empty').style.display = items.length ? 'none' : 'block';
    items.forEach(t => {
      const row = document.createElement('div');
      row.className = 'sr-row';
      row.innerHTML = `<div class="srm"><div class="srt">${escapeHtml(t.title || '')}</div>
        <div class="sra">${escapeHtml(t.artists || '')}</div></div>
        <button class="sr-play" aria-label="Reproducir">${PLAY_ICON}</button>`;
      row.querySelector('.sr-play').onclick = () => playTrack(t);
      list.appendChild(row);
    });
  } catch (e) {}
}

/* ---------- WhatsApp ---------- */
let waCurrentChat = null;
let waPollTimer = null;

function fmtWaTime(ts) {
  if (!ts) return '';
  const d = new Date(ts > 2e12 ? ts : ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.getHours() + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  return d.getDate() + '/' + (d.getMonth() + 1);
}
function initials(name) {
  return (name || '?').trim().slice(0, 2).toUpperCase();
}

async function loadChats() {
  if (currentScreen !== 'whatsapp' || waCurrentChat) return;
  try {
    const res = await fetch(`/api/whatsapp/chats?token=${TOKEN}`);
    const wrap = document.getElementById('wa-chats');
    const empty = document.getElementById('wa-chats-empty');
    if (res.status === 503) {
      empty.textContent = 'WhatsApp no está emparejado todavía. Empareja el bridge desde el escritorio.';
      empty.style.display = 'block';
      wrap.innerHTML = '';
      return;
    }
    if (!res.ok) return;
    const chats = await res.json();
    empty.style.display = chats.length ? 'none' : 'block';
    if (!chats.length) empty.textContent = 'No hay chats recientes.';
    wrap.innerHTML = '';
    chats.forEach(c => {
      const row = document.createElement('div');
      row.className = 'wa-row';
      row.innerHTML = `<div class="wa-avatar">${escapeHtml(initials(c.name))}</div>
        <div class="wa-meta">
          <div class="wa-top"><span class="wa-name">${escapeHtml(c.name || c.chatId)}</span>
            <span class="wa-time">${fmtWaTime(c.timestamp)}</span></div>
          <div class="wa-preview">${escapeHtml((c.fromMe ? 'Tú: ' : '') + (c.preview || ''))}</div>
        </div>
        ${c.unread ? `<span class="wa-badge">${c.unread}</span>` : ''}`;
      row.onclick = () => openChat(c.chatId, c.name || c.chatId);
      wrap.appendChild(row);
    });
  } catch (e) {}
  if (currentScreen === 'whatsapp' && !waCurrentChat) setTimeout(loadChats, 4000);
}

function openChat(chatId, name) {
  waCurrentChat = chatId;
  document.getElementById('wa-thread-name').textContent = name;
  document.getElementById('wa-list-view').style.display = 'none';
  document.getElementById('wa-thread-view').style.display = 'flex';
  document.getElementById('wa-messages').innerHTML = '';
  loadWaMessages();
  clearTimeout(waPollTimer);
  waPollTimer = setTimeout(pollWaMessages, 3000);
}

function waBack() {
  waCurrentChat = null;
  clearTimeout(waPollTimer);
  document.getElementById('wa-thread-view').style.display = 'none';
  document.getElementById('wa-list-view').style.display = 'flex';
  loadChats();
}

async function loadWaMessages() {
  if (!waCurrentChat) return;
  try {
    const res = await fetch(`/api/whatsapp/messages?chat_id=${encodeURIComponent(waCurrentChat)}&token=${TOKEN}`);
    if (!res.ok) return;
    const msgs = await res.json();
    const wrap = document.getElementById('wa-messages-wrap');
    const list = document.getElementById('wa-messages');
    const atBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 80;
    list.innerHTML = '';
    msgs.forEach(m => {
      const div = document.createElement('div');
      div.className = 'msg ' + (m.fromMe ? 'you' : 'jarvis');
      const meta = m.fromMe ? '' : `<div class="meta">${escapeHtml(m.authorName || m.senderName || '')}</div>`;
      div.innerHTML = meta + escapeHtml(m.body || (m.hasMedia ? '[archivo adjunto]' : ''));
      list.appendChild(div);
    });
    wrap.scrollTop = wrap.scrollHeight;
  } catch (e) {}
}
function pollWaMessages() {
  if (!waCurrentChat || currentScreen !== 'whatsapp') return;
  loadWaMessages();
  waPollTimer = setTimeout(pollWaMessages, 3000);
}

async function waSend() {
  const input = document.getElementById('wa-input');
  const text = input.value.trim();
  if (!text || !waCurrentChat) return;
  input.value = '';
  const btn = document.getElementById('wa-send-btn');
  btn.disabled = true;
  try {
    await fetch(`/api/whatsapp/send?token=${TOKEN}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ chat_id: waCurrentChat, text }),
    });
    setTimeout(loadWaMessages, 600);
  } catch (e) {}
  btn.disabled = false;
  input.focus();
}
document.getElementById('wa-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    waSend();
  }
});

const volSlider = document.getElementById('vol-slider');
let volDebounce = null;
volSlider.addEventListener('input', () => {
  userDraggingVolume = true;
  clearTimeout(volDebounce);
  volDebounce = setTimeout(() => {
    musicAction('volume', { level: parseInt(volSlider.value, 10) });
  }, 150);
});
volSlider.addEventListener('change', () => { setTimeout(() => userDraggingVolume = false, 800); });

function copyUrl() {
  const btn = document.getElementById('copy-url-btn');
  const url = location.href;
  const done = () => {
    btn.textContent = 'Enlace copiado';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copiar enlace del panel'; btn.classList.remove('copied'); }, 1800);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(done).catch(done);
  } else {
    done();
  }
}

document.getElementById('st-host').textContent = location.host;

pollLog();
pollStatus();
requestAnimationFrame(tickProgress);
</script>
</body></html>"""


def _live_playback() -> dict:
    """Transport state read straight from the headless player.

    The desktop window mirrors this too, but through a 1 s poller whose result
    is applied on the Qt GUI thread — so right after the phone presses next or
    pause, the mirror still describes the previous state and the phone's own
    follow-up poll shows its button snapping back. The desktop looked right, the
    phone looked wrong, for the same action. Reading the player directly removes
    that lag entirely.

    Returns {} when there is no headless player (GUI backend, or nothing has
    played yet), so the window mirror stays the fallback.
    """
    try:
        from actions import ytmusic_headless
        # current() falls back to a live IPC probe (ten round-trips) when the
        # polling worker isn't running, so don't call it just to be told that
        # nothing has ever played.
        meta = ytmusic_headless._last_meta
        if not (meta.get("title") or meta.get("videoId")):
            return {}
        info = ytmusic_headless.current() or {}
    except Exception:
        return {}
    if not isinstance(info, dict):
        return {}
    # An empty title AND no video id means this backend isn't the one playing.
    if not (info.get("title") or info.get("videoId")):
        return {}
    return info


def _make_app(token: str):
    from flask import Flask, jsonify, request, Response, send_file

    app = Flask(__name__)

    def _check_token() -> bool:
        return request.args.get("token", "") == token

    @app.route("/")
    def index():
        if not _check_token():
            return "Unauthorized", 401
        return _PAGE_HTML

    @app.route("/api/log")
    def api_log():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        try:
            since = int(request.args.get("since", "0"))
        except ValueError:
            since = 0
        with _buffer_lock:
            items = [e for e in _log_buffer if e["id"] > since]
        return jsonify(items)

    @app.route("/api/status")
    def api_status():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        win = getattr(_ui_ref, "_win", None)
        live = _live_playback()
        video_id = str(live.get("videoId") or getattr(win, "_play_video_id", "") or "")
        # Prefer the real per-track album art (square, from ytmusicapi) over
        # the raw i.ytimg.com video-frame thumbnail, which is 4:3 and shows
        # letterboxing/video content instead of cover art (only used when the
        # real one isn't known yet, e.g. right when a track starts loading).
        win_thumbnail = str(getattr(win, "_play_thumbnail", "") or "")
        if live and video_id != str(getattr(win, "_play_video_id", "") or ""):
            # The window is a track behind: its artwork is the previous cover.
            win_thumbnail = ""
        thumbnail = str(live.get("thumbnail") or win_thumbnail)
        if not thumbnail and video_id:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        elif thumbnail:
            # This one is shown big; the stored URL is the 120² list version.
            try:
                from actions.ytmusic import upgrade_thumbnail_url
                thumbnail = upgrade_thumbnail_url(thumbnail, _COVER_NOW_PLAYING)
            except Exception:
                pass
        music = {
            "title": str(live.get("title") or getattr(win, "_play_title", "")),
            "artists": str(live.get("artists") or getattr(win, "_play_artists", "")),
            "playing": bool(live["playing"]) if "playing" in live
            else bool(getattr(win, "_play_playing", False)),
            "state": str(live.get("state") or getattr(win, "_play_state", "stopped")),
            "position": float(live.get("position", getattr(win, "_play_position", 0)) or 0),
            "duration": float(live.get("duration", getattr(win, "_play_duration", 0)) or 0),
            "volume": int(live.get("volume", getattr(win, "_music_volume_level", 100)) or 0),
            "videoId": video_id,
            "thumbnail": thumbnail,
            # Drives the tint of the phone's media notification.
            "coverColor": cover_accent_color(thumbnail),
            "liked": bool(getattr(win, "_play_liked", False)),
        }
        return jsonify({
            "running": True,
            "now": time.time(),
            "music": music,
            "mode": getattr(win, "_active_mode", ""),
            "muted": bool(getattr(win, "_muted", False)),
        })

    @app.route("/api/command", methods=["POST"])
    def api_command():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"error": "empty_text"}), 400

        jarvis = _jarvis_ref
        loop = getattr(jarvis, "_loop", None)
        session = getattr(jarvis, "session", None)
        if jarvis is None or loop is None or session is None:
            return jsonify({"error": "not_ready"}), 503

        _on_log_event({"source": "phone", "message": f"Tú: {text}"})

        import asyncio
        asyncio.run_coroutine_threadsafe(jarvis._send_text_command(text), loop)
        return jsonify({"ok": True}), 202

    @app.route("/api/music/<action>", methods=["POST"])
    def api_music(action):
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        if action not in _MUSIC_ACTIONS:
            return jsonify({"error": "unknown_action"}), 400
        ctrl = getattr(_ui_ref, "playback_controller", None)
        if ctrl is None:
            return jsonify({"error": "not_ready"}), 503
        params = request.get_json(silent=True) or {}
        try:
            future = ctrl.submit(action, params)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        try:
            result = future.result(timeout=_MUSIC_ACTION_WAIT_SECONDS)
        except FuturesTimeout:
            # Still running. The command is queued and will finish; the phone
            # reconciles from its status poll, which is more useful than an
            # error it would have to guess the meaning of.
            return jsonify({"ok": True, "pending": True}), 202
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "result": str(result)})

    @app.route("/api/music/prefetch", methods=["POST"])
    def api_music_prefetch():
        """Warm the streams for tracks the phone is showing.

        Deliberately NOT a playback action: it runs off the serialized worker
        (it only spawns resolver threads), so warm-ups can never sit in front
        of a transport command. This is what the desktop gets for free when you
        select a row before double-clicking it — on the phone a tap *is* the
        play, so without this every tap pays the full ~2-3 s resolution.
        """
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        raw = data.get("tracks") or data.get("video_ids") or []
        tracks = []
        for item in raw:
            if isinstance(item, dict):
                vid = str(item.get("videoId") or item.get("video_id") or "").strip()
            else:
                vid = str(item or "").strip()
            if vid:
                tracks.append({"videoId": vid})
        if not tracks:
            return jsonify({"scheduled": 0})
        try:
            from actions import ytmusic_headless
            scheduled = 0
            # One resolution per candidate, capped: this runs on every list the
            # phone paints, and a burst of extractions is how you get throttled.
            for track in tracks[:_MAX_PHONE_PREFETCH]:
                scheduled += int(
                    ytmusic_headless.prefetch_tracks([track], 0, 1).get("scheduled", 0)
                )
            return jsonify({"scheduled": scheduled})
        except Exception as e:
            print(f"[LanDashboard] prefetch failed: {e}")
            return jsonify({"scheduled": 0})

    @app.route("/api/music/playlists")
    def api_music_playlists():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        try:
            from actions import ytmusic
            return jsonify(_with_cover(ytmusic.list_playlists() or [], _COVER_GRID))
        except Exception as e:
            print(f"[LanDashboard] list_playlists failed: {e}")
            return jsonify([])

    @app.route("/api/music/playlist_tracks")
    def api_music_playlist_tracks():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        playlist_id = (request.args.get("playlist_id") or "").strip()
        if not playlist_id:
            return jsonify({"error": "missing_playlist_id"}), 400
        try:
            limit = int(request.args.get("limit", "200"))
        except ValueError:
            limit = 200
        try:
            from actions import ytmusic
            return jsonify(_with_cover(ytmusic.list_playlist_tracks(playlist_id, limit=limit) or [], _COVER_ROW))
        except Exception as e:
            print(f"[LanDashboard] list_playlist_tracks failed: {e}")
            return jsonify([])

    @app.route("/api/music/search")
    def api_music_search():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "empty_query"}), 400
        try:
            from actions import ytmusic
            return jsonify(_with_cover(ytmusic.search_songs(q, limit=20) or [], _COVER_ROW))
        except Exception as e:
            print(f"[LanDashboard] search_songs failed: {e}")
            return jsonify([])

    @app.route("/api/music/search_artists")
    def api_music_search_artists():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "empty_query"}), 400
        try:
            from actions import ytmusic
            return jsonify(_with_cover(ytmusic.search_artists(q, limit=10) or [], _COVER_ROW))
        except Exception as e:
            print(f"[LanDashboard] search_artists failed: {e}")
            return jsonify([])

    @app.route("/api/music/artist")
    def api_music_artist():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        browse_id = (request.args.get("browse_id") or "").strip()
        if not browse_id:
            return jsonify({"error": "missing_browse_id"}), 400
        try:
            from actions import ytmusic
            info = ytmusic.get_artist_details(browse_id=browse_id) or {}
            if info.get("thumbnail"):
                info["thumbnail"] = ytmusic.upgrade_thumbnail_url(str(info["thumbnail"]), _COVER_GRID)
            for key in ("top_songs", "recommendations", "videos"):
                _with_cover(info.get(key) or [], _COVER_ROW)
            for key in ("albums", "singles", "related"):
                _with_cover(info.get(key) or [], _COVER_GRID)
            return jsonify(info)
        except Exception as e:
            print(f"[LanDashboard] get_artist_details failed: {e}")
            return jsonify({"error": str(e)}), 502

    @app.route("/api/music/queue")
    def api_music_queue():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        try:
            limit = int(request.args.get("limit", "300"))
        except ValueError:
            limit = 300
        try:
            from actions import ytmusic_headless
            return jsonify(_with_cover(ytmusic_headless.queue_snapshot(limit=limit), _COVER_ROW))
        except Exception as e:
            print(f"[LanDashboard] queue_snapshot failed: {e}")
            return jsonify([])

    @app.route("/api/whatsapp/chats")
    def api_whatsapp_chats():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        from actions import whatsapp
        try:
            chats = whatsapp.list_recent_chats(
                limit=100, timeout=10, include_pictures=False, raise_on_unready=True,
            )
            return jsonify(chats or [])
        except whatsapp.WhatsAppUnavailable:
            return jsonify({"error": "not_ready"}), 503
        except Exception as e:
            print(f"[LanDashboard] list_recent_chats failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/messages")
    def api_whatsapp_messages():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        chat_id = (request.args.get("chat_id") or "").strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        try:
            from actions import whatsapp
            msgs = whatsapp.get_conversation(chat_id, limit=50, timeout=25, strict=True)
            return jsonify(msgs or [])
        except Exception as e:
            print(f"[LanDashboard] get_conversation failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/send", methods=["POST"])
    def api_whatsapp_send():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        chat_id = str(data.get("chat_id", "")).strip()
        text = str(data.get("text", "")).strip()
        if not chat_id or not text:
            return jsonify({"error": "missing_fields"}), 400
        try:
            from actions import whatsapp
            result = whatsapp.send_whatsapp(to=chat_id, body=text)
            return jsonify({"ok": True, "result": result})
        except Exception as e:
            print(f"[LanDashboard] send_whatsapp failed: {e}")
            return jsonify({"error": str(e)}), 500

    def _retro_screen():
        """The live emulator screen, or None. Imported lazily: ui.widgets.retro
        pulls in PyQt, which must not be a hard dependency of the API layer."""
        try:
            from ui.widgets import retro
            return retro.active_screen()
        except Exception:
            return None

    @app.route("/api/gamepad/status")
    def api_gamepad_status():
        """Lets the phone offer to become a controller exactly while a game is
        actually running, instead of making the user go looking for it."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        screen = _retro_screen()
        if screen is None:
            return jsonify({"active": False, "console": "", "buttons": [], "announce": _gamepad_announce})
        console_id = str(getattr(screen, "_console_id", "") or "")
        try:
            from actions.emulator_runtime import pad_layout
            layout = pad_layout(console_id)
        except Exception:
            layout = {}
        return jsonify({
            "active": True,
            "console": console_id,
            "buttons": sorted(_GAMEPAD_BUTTONS),
            # Sticks/triggers exist only on some consoles; the phone draws the
            # pad from this instead of showing a PS2 layout for a Game Boy.
            "layout": layout,
            # Bumped by /api/gamepad/announce. The phone re-opens its prompt
            # when this changes, so the desktop can call the pad back after
            # the user dismissed it.
            "announce": _gamepad_announce,
        })

    @app.route("/api/gamepad/announce", methods=["POST"])
    def api_gamepad_announce():
        """Ask the phone to show the 'use me as a controller' prompt again."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        return jsonify({"ok": True, "announce": bump_gamepad_announce()})

    @app.route("/api/gamepad/input", methods=["POST"])
    def api_gamepad_input():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        screen = _retro_screen()
        core = getattr(screen, "core", None) if screen is not None else None
        if core is None:
            return jsonify({"error": "no_game"}), 409

        data = request.get_json(silent=True) or {}
        try:
            for entry in data.get("buttons") or []:
                name = str(entry.get("name", "")).strip().lower()
                if name in _GAMEPAD_BUTTONS:
                    core.set_button(name, bool(entry.get("pressed")))
            for entry in data.get("axes") or []:
                core.set_axis(
                    int(entry.get("index", 0)),
                    int(entry.get("axis", 0)),
                    int(entry.get("value", 0)),
                )
            if data.get("clear"):
                core.clear_input()
        except Exception as e:
            print(f"[LanDashboard] gamepad input failed: {e}")
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True})

    @app.route("/api/voice", methods=["POST"])
    def api_voice():
        """Transcribe an audio clip recorded on the phone, and optionally run it
        as a spoken command — the phone has no wake word, so the recording IS
        the utterance."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        upload = request.files.get("audio")
        if upload is None or not upload.filename:
            return jsonify({"error": "missing_audio"}), 400

        import tempfile
        from pathlib import Path
        suffix = Path(upload.filename).suffix or ".m4a"
        tmp = Path(tempfile.gettempdir()) / f"jarvis-voice-{int(time.time() * 1000)}{suffix}"
        try:
            upload.save(str(tmp))
            from actions.file_processor import _process_audio
            text = str(_process_audio(tmp, "transcribe", {}) or "").strip()
        except Exception as e:
            print(f"[LanDashboard] voice transcription failed: {e}")
            return jsonify({"error": str(e)}), 500
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

        if not text:
            return jsonify({"ok": True, "text": "", "ran": False})

        ran = False
        if str(request.form.get("run", "1")) not in ("0", "false", "False"):
            jarvis = _jarvis_ref
            loop = getattr(jarvis, "_loop", None)
            session = getattr(jarvis, "session", None)
            if jarvis is not None and loop is not None and session is not None:
                _on_log_event({"source": "phone", "message": f"Tú: {text}"})
                import asyncio
                asyncio.run_coroutine_threadsafe(jarvis._send_text_command(text), loop)
                ran = True

        return jsonify({"ok": True, "text": text, "ran": ran})

    @app.route("/api/remote/clipboard")
    def api_remote_clipboard_get():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        try:
            import pyperclip
            return jsonify({"text": pyperclip.paste() or ""})
        except Exception as e:
            print(f"[LanDashboard] clipboard read failed: {e}")
            return jsonify({"error": str(e)}), 503

    @app.route("/api/remote/clipboard", methods=["POST"])
    def api_remote_clipboard_set():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", ""))
        try:
            import pyperclip
            pyperclip.copy(text)
            return jsonify({"ok": True})
        except Exception as e:
            print(f"[LanDashboard] clipboard write failed: {e}")
            return jsonify({"error": str(e)}), 503

    @app.route("/api/remote/system", methods=["POST"])
    def api_remote_system():
        """Desktop controls the phone can drive. Whitelisted by name: this must
        never become a way to call arbitrary functions over the network."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        action = str(data.get("action", "")).strip()
        if action not in _REMOTE_ACTIONS:
            return jsonify({"error": "unknown_action"}), 400
        try:
            from actions import computer_settings, system_tools
            if action == "volume_set":
                computer_settings.volume_set(int(data.get("level", 50)))
            elif action == "app_launch":
                name = str(data.get("name", "")).strip()
                if not name:
                    return jsonify({"error": "missing_name"}), 400
                return jsonify({"ok": True, "result": str(system_tools.app_launch(name))})
            else:
                getattr(computer_settings, action)()
            return jsonify({"ok": True})
        except Exception as e:
            print(f"[LanDashboard] remote action '{action}' failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/remote/status")
    def api_remote_status():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        try:
            from actions import system_tools
            return jsonify(system_tools.system_status())
        except Exception as e:
            print(f"[LanDashboard] system status failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/music/lyrics")
    def api_music_lyrics():
        """Time-stamped lyrics for a track, as `[{time, line}]`.

        `time` is the second the line starts. An empty list simply means no
        provider had synced lyrics for it — a common, non-exceptional outcome.
        """
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        title = (request.args.get("title") or "").strip()
        artists = (request.args.get("artists") or "").strip()
        if not title:
            return jsonify({"error": "missing_title"}), 400
        try:
            from actions.lyrics import get_synced_lyrics
            lines = get_synced_lyrics(title, artists) or ()
            return jsonify([{"time": float(t), "line": str(line)} for t, line in lines])
        except Exception as e:
            print(f"[LanDashboard] lyrics failed: {e}")
            return jsonify([])

    @app.route("/api/music/stream")
    def api_music_stream():
        """Serve a track's audio so the phone can be the speaker instead of the PC.

        Two sources, in order: a file already downloaded into the offline
        library (fast, no network), otherwise the stream yt-dlp resolves for
        the desktop player — proxied rather than redirected, because those URLs
        are tied to the session/IP that resolved them and often refuse a
        different client.

        Range support is mandatory here: without it Android's player cannot
        seek, and it may refuse to start at all on longer tracks.
        """
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        video_id = (request.args.get("video_id") or "").strip()
        if not video_id:
            return jsonify({"error": "missing_video_id"}), 400

        try:
            from actions.offline_library import local_file_for
            local_path = local_file_for(video_id)
        except Exception:
            local_path = None
        if local_path:
            # send_file(conditional=True) implements Range/206 for us.
            return send_file(local_path, conditional=True)

        try:
            from actions import ytmusic_headless as yh
            url, _duration = yh._resolve_stream_for_video(video_id)
        except Exception as e:
            print(f"[LanDashboard] stream resolve failed: {e}")
            return jsonify({"error": str(e)}), 502
        if not url:
            return jsonify({"error": "unresolved"}), 404

        import requests
        headers = {}
        client_range = request.headers.get("Range")
        if client_range:
            headers["Range"] = client_range
        try:
            upstream = requests.get(url, headers=headers, stream=True, timeout=20)
        except Exception as e:
            print(f"[LanDashboard] stream proxy failed: {e}")
            return jsonify({"error": str(e)}), 502

        passthrough = {}
        for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            if key in upstream.headers:
                passthrough[key] = upstream.headers[key]
        passthrough.setdefault("Accept-Ranges", "bytes")

        return Response(
            upstream.iter_content(chunk_size=64 * 1024),
            status=upstream.status_code,
            headers=passthrough,
            mimetype=upstream.headers.get("Content-Type", "audio/mpeg"),
        )

    @app.route("/api/whatsapp/automations")
    def api_whatsapp_automations():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        try:
            from actions import app_settings, whatsapp_rules
            return jsonify({
                "settings": {
                    key: bool(app_settings.get(key, False))
                    for key in _WA_SETTING_KEYS
                },
                "rules": whatsapp_rules.load_rules(),
            })
        except Exception as e:
            print(f"[LanDashboard] whatsapp automations read failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/automations/setting", methods=["POST"])
    def api_whatsapp_automation_setting():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        key = str(data.get("key", "")).strip()
        # Whitelisted on purpose: this endpoint must not become a generic
        # "write any app setting over the LAN" hole.
        if key not in _WA_SETTING_KEYS:
            return jsonify({"error": "unknown_setting"}), 400
        try:
            from actions import app_settings
            app_settings.set(key, bool(data.get("value")))
            return jsonify({"ok": True, "key": key, "value": bool(data.get("value"))})
        except Exception as e:
            print(f"[LanDashboard] whatsapp setting write failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/rules/save", methods=["POST"])
    def api_whatsapp_rule_save():
        """Creates when `rule.id` is absent, updates in place when present."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        rule = data.get("rule")
        if not isinstance(rule, dict):
            return jsonify({"error": "missing_rule"}), 400
        try:
            from actions import whatsapp_rules
            rule_id = str(rule.get("id") or "").strip()
            if rule_id and whatsapp_rules.update_rule(rule_id, rule):
                return jsonify({"ok": True, "created": False})
            return jsonify({"ok": True, "created": True, "rule": whatsapp_rules.add_rule(rule)})
        except Exception as e:
            print(f"[LanDashboard] whatsapp rule save failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/rules/delete", methods=["POST"])
    def api_whatsapp_rule_delete():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        rule_id = str(data.get("rule_id", "")).strip()
        if not rule_id:
            return jsonify({"error": "missing_rule_id"}), 400
        try:
            from actions import whatsapp_rules
            return jsonify({"ok": whatsapp_rules.delete_rule(rule_id)})
        except Exception as e:
            print(f"[LanDashboard] whatsapp rule delete failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/rules/move", methods=["POST"])
    def api_whatsapp_rule_move():
        """Rule order IS priority — the first match wins."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        rule_id = str(data.get("rule_id", "")).strip()
        if not rule_id:
            return jsonify({"error": "missing_rule_id"}), 400
        try:
            delta = int(data.get("delta", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "bad_delta"}), 400
        try:
            from actions import whatsapp_rules
            return jsonify({"ok": whatsapp_rules.move_rule(rule_id, delta)})
        except Exception as e:
            print(f"[LanDashboard] whatsapp rule move failed: {e}")
            return jsonify({"error": str(e)}), 500

    def _calendar_error(exc: Exception):
        """Google auth failures are the normal 'not set up yet' case, not a
        server fault — the app shows a sign-in hint instead of a red error."""
        text = str(exc)
        lowered = text.lower()
        auth_ish = any(
            k in lowered
            for k in ("credentials", "invalid_grant", "token", "unauthorized", "401", "403")
        ) or isinstance(exc, FileNotFoundError)
        print(f"[LanDashboard] calendar call failed: {text}")
        return jsonify({"error": text, "needs_auth": auth_ish}), (503 if auth_ish else 500)

    @app.route("/api/calendar/events")
    def api_calendar_events():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        time_min = (request.args.get("time_min") or "").strip()
        time_max = (request.args.get("time_max") or "").strip()
        if not time_min or not time_max:
            return jsonify({"error": "missing_range"}), 400
        try:
            from actions import google_calendar as gcal
            return jsonify(gcal.list_events_range(time_min, time_max) or [])
        except Exception as e:
            return _calendar_error(e)

    @app.route("/api/calendar/create", methods=["POST"])
    def api_calendar_create():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        summary = str(data.get("summary", "")).strip()
        start = str(data.get("start", "")).strip()
        if not summary or not start:
            return jsonify({"error": "missing_fields"}), 400
        try:
            from actions import google_calendar as gcal
            created = gcal.create_event(
                summary=summary,
                start=start,
                end=str(data.get("end", "")).strip() or None,
                description=str(data.get("description", "")),
                location=str(data.get("location", "")),
            )
            return jsonify({"ok": True, "event": created})
        except Exception as e:
            return _calendar_error(e)

    @app.route("/api/calendar/update", methods=["POST"])
    def api_calendar_update():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        event_id = str(data.get("event_id", "")).strip()
        if not event_id:
            return jsonify({"error": "missing_event_id"}), 400
        try:
            from actions import google_calendar as gcal
            updated = gcal.update_event(
                event_id,
                summary=data.get("summary"),
                start=data.get("start"),
                end=data.get("end"),
                description=data.get("description"),
                location=data.get("location"),
            )
            return jsonify({"ok": True, "event": updated})
        except Exception as e:
            return _calendar_error(e)

    @app.route("/api/calendar/delete", methods=["POST"])
    def api_calendar_delete():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        event_id = str(data.get("event_id", "")).strip()
        if not event_id:
            return jsonify({"error": "missing_event_id"}), 400
        try:
            from actions import google_calendar as gcal
            return jsonify({"ok": True, "result": gcal.delete_event(event_id)})
        except Exception as e:
            return _calendar_error(e)

    @app.route("/api/whatsapp/translate", methods=["POST"])
    def api_whatsapp_translate():
        """Inline translation of one message, same helper the desktop uses.

        Returns an empty string when the text is already in the target
        language — that is the helper's way of saying "nothing to show".
        """
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"error": "empty_text"}), 400
        try:
            from actions.whatsapp_ai import translate_if_foreign
            return jsonify({"ok": True, "text": translate_if_foreign(text)})
        except Exception as e:
            print(f"[LanDashboard] translate failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/mark_read", methods=["POST"])
    def api_whatsapp_mark_read():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        chat_id = str(data.get("chat_id", "")).strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        try:
            from actions import whatsapp
            return jsonify({"ok": bool(whatsapp.mark_chat_read(chat_id))})
        except Exception as e:
            print(f"[LanDashboard] mark_read failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/acks", methods=["POST"])
    def api_whatsapp_acks():
        """Delivery/read state for messages we sent, as `{id: ack}`.

        WhatsApp's ack ladder: 1 sent, 2 delivered, 3 read, 4 played.
        """
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        ids = [str(i) for i in (data.get("ids") or []) if str(i).strip()]
        if not ids:
            return jsonify({})
        try:
            from actions import whatsapp
            return jsonify(whatsapp.get_message_acks(ids) or {})
        except Exception as e:
            print(f"[LanDashboard] acks failed: {e}")
            return jsonify({})

    @app.route("/api/whatsapp/transcribe", methods=["POST"])
    def api_whatsapp_transcribe():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        message = (request.get_json(silent=True) or {}).get("message")
        if not isinstance(message, dict):
            return jsonify({"error": "missing_message"}), 400
        try:
            from actions import whatsapp
            text = whatsapp.transcribe_message_audio(message)
            return jsonify({"ok": True, "text": str(text or "")})
        except Exception as e:
            print(f"[LanDashboard] transcribe failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/suggest", methods=["POST"])
    def api_whatsapp_suggest():
        """Draft a reply in the user's tone, without sending it."""
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        chat_id = str(data.get("chat_id", "")).strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        try:
            from actions.whatsapp_ai import generate_whatsapp_reply
            draft = generate_whatsapp_reply(chat_id, str(data.get("incoming", "")))
            return jsonify({"ok": True, "text": str(draft or "")})
        except Exception as e:
            print(f"[LanDashboard] suggest failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/send_media", methods=["POST"])
    def api_whatsapp_send_media():
        """Upload a file from the phone and send it to a chat.

        The bridge only takes a local path, so the upload is written to a temp
        file on the desktop first and removed once the send returns.
        """
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        chat_id = str(request.form.get("chat_id", "")).strip()
        upload = request.files.get("file")
        if not chat_id or upload is None or not upload.filename:
            return jsonify({"error": "missing_fields"}), 400

        import tempfile
        from pathlib import Path
        safe_name = Path(upload.filename).name
        tmp = Path(tempfile.gettempdir()) / f"jarvis-wa-{int(time.time() * 1000)}-{safe_name}"
        try:
            upload.save(str(tmp))
            from actions import whatsapp
            result = whatsapp.send_whatsapp_media(
                to=chat_id,
                file_path=str(tmp),
                caption=str(request.form.get("caption", "")),
            )
            return jsonify({"ok": True, "result": result})
        except Exception as e:
            print(f"[LanDashboard] send_media failed: {e}")
            return jsonify({"error": str(e)}), 500
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    @app.route("/api/whatsapp/media")
    def api_whatsapp_media():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        media_url = (request.args.get("url") or "").strip()
        if not media_url:
            return jsonify({"error": "missing_url"}), 400
        # Fetch directly (not via whatsapp.download_message_media, which only
        # returns raw bytes) so we can pass through the bridge's OWN
        # Content-Type — it always knows the real mimetype of whatever it
        # just downloaded from WhatsApp, which is more reliable than the
        # pre-download mimetype metadata the phone sent us (often null for
        # images/stickers until the media is actually fetched).
        import requests
        from actions import whatsapp
        try:
            resolved = whatsapp.media_url(media_url)
            resp = requests.get(resolved, headers=whatsapp._request_headers(), timeout=15)
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type") or request.args.get("mimetype") or "application/octet-stream"
            return Response(resp.content, mimetype=ctype)
        except Exception as e:
            print(f"[LanDashboard] media proxy failed: {e}")
            return jsonify({"error": str(e)}), 502

    @app.route("/api/whatsapp/avatar")
    def api_whatsapp_avatar():
        if not _check_token():
            return jsonify({"error": "unauthorized"}), 403
        chat_id = (request.args.get("chat_id") or "").strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        import requests
        from actions import whatsapp
        try:
            url = whatsapp.get_profile_picture_url(chat_id)
            if not url:
                return jsonify({"error": "no_picture"}), 404
            resolved = whatsapp.media_url(url)
            resp = requests.get(resolved, headers=whatsapp._request_headers(), timeout=8)
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type") or "image/jpeg"
            return Response(resp.content, mimetype=ctype)
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    return app


def start_dashboard(port: int | None = None) -> str:
    """Starts the dashboard in a daemon thread (idempotent — calling twice
    is a no-op). Returns the pairing URL: http://<ip>:<port>/?token=...,
    where <ip> is the LAN IP, or the public IP when
    "lan_dashboard_public_mode" is on (see get_public_ip's docstring — the
    router must already be forwarding <port> to this machine)."""
    global _app, _started

    port = port or int(app_settings.get("lan_dashboard_port", 8765))
    token = _get_or_create_token()

    if not _started:
        event_bus.subscribe(event_bus.ActionEvent.LOG, _on_log_event)
        event_bus.subscribe(event_bus.ActionEvent.TOAST, _on_toast_event)
        event_bus.subscribe(event_bus.ActionEvent.ERROR, _on_error_event)

        _app = _make_app(token)
        threading.Thread(
            target=lambda: _app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True),
            daemon=True,
        ).start()
        _started = True

    ip = get_lan_ip()
    if app_settings.get("lan_dashboard_public_mode", False):
        ip = get_public_ip() or app_settings.get("lan_dashboard_last_public_ip", "") or ip
    return f"http://{ip}:{port}/?token={token}"
