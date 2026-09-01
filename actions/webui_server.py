"""Loopback-only Flask backend for the new desktop web UI (pywebview).

Unlike actions/lan_dashboard.py (opt-in, binds 0.0.0.0 for phone/LAN
access, token-gated), this server is the primary desktop UI's own backend:
it always binds 127.0.0.1 and is never meant to be reachable off-box, so it
carries no token. Serves the WhatsApp/Music REST API for the MVP panels,
a Server-Sent Events stream that mirrors actions/event_bus.py plus
playback/whatsapp push updates, and the built webui/dist/ static assets in
production.

Route handlers call the same actions.whatsapp / actions.ytmusic /
actions.ytmusic_headless functions actions/lan_dashboard.py already calls —
business logic lives there, not here.
"""
from __future__ import annotations

import collections
import json
import queue
import threading
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from actions import app_settings
from actions import paths

_app = None
_started = False
_host_ref = None
_jarvis_ref = None

_MUSIC_ACTIONS = {
    "play", "pause", "toggle", "next", "previous", "volume", "seek",
    "play_playlist", "play_tracks", "play_track", "jump_to", "set_like",
    "resume",
    # Live-pushed from Settings (ui/panels/settings.py's Música/Ecualizador
    # tabs) to the running playback backend — same action names main.py's
    # dispatcher already handles (main.py:3795-3831).
    "set_crossfade", "set_crossfade_on_skip", "set_autoplay",
    "set_audio_quality", "set_ducking", "set_audio_output_device",
    "set_equalizer",
}
_MUSIC_ACTION_WAIT_SECONDS = 2.0

_subscribers_lock = threading.Lock()
_subscribers: "set[queue.Queue]" = set()

# Activity feed history (right panel log) — same coalescing-buffer pattern
# as actions/lan_dashboard.py's _log_buffer, so a client that connects late
# or reloads the page can back-fill via GET /api/log instead of only ever
# seeing events from the moment it opened the SSE stream.
_log_buffer_lock = threading.Lock()
_log_buffer: "collections.deque[dict]" = collections.deque(maxlen=500)
_next_log_id = 0


def _append_log_entry(entry_type: str, message: str) -> None:
    global _next_log_id
    with _log_buffer_lock:
        if _log_buffer and _log_buffer[-1]["type"] == entry_type \
                and _log_buffer[-1]["message"] == message:
            return
        _next_log_id += 1
        _log_buffer.append({"id": _next_log_id, "type": entry_type, "message": message})


def publish(event_type: str, data: dict) -> None:
    """Fan out an SSE event to every connected /api/events client.

    Non-blocking: a slow/stuck client drops events (bounded queue) rather
    than stalling the caller, which may be running on the voice/agent loop
    thread.
    """
    if event_type in ("log", "toast"):
        _append_log_entry(event_type, str(data.get("text", "")))

    payload = json.dumps({"type": event_type, "data": data})
    with _subscribers_lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def _sse_stream():
    q: "queue.Queue[str]" = queue.Queue(maxsize=200)
    with _subscribers_lock:
        _subscribers.add(q)
    try:
        # Let a freshly-opened EventSource render something immediately
        # instead of waiting for the next backend event.
        yield "event: ready\ndata: {}\n\n"
        while True:
            payload = q.get()
            yield f"data: {payload}\n\n"
    finally:
        with _subscribers_lock:
            _subscribers.discard(q)


def _google_error(exc: Exception):
    """Google auth failures are the normal 'not set up yet' case, not a
    server fault — same classification actions/lan_dashboard.py's
    _calendar_error uses, shared here across Gmail/Drive/Calendar so the
    frontend can show one consistent 'sign in with Google' hint."""
    from flask import jsonify

    text = str(exc)
    lowered = text.lower()
    auth_ish = any(
        k in lowered
        for k in ("credentials", "invalid_grant", "token", "unauthorized", "401", "403")
    ) or isinstance(exc, FileNotFoundError)
    print(f"[WebUIServer] Google API call failed: {text}")
    return jsonify({"error": text, "needs_auth": auth_ish}), (503 if auth_ish else 500)


def _make_app(host_ref):
    from flask import Flask, jsonify, request, Response, send_from_directory

    app = Flask(__name__)
    dist_dir = paths.resource("webui", "dist")

    @app.route("/")
    def index():
        if dist_dir.exists():
            return send_from_directory(dist_dir, "index.html")
        return (
            "JARVIS web UI: webui/dist not built yet. "
            "Run `npm --prefix webui run dev` and set JARVIS_WEBUI_DEV_URL, "
            "or `npm --prefix webui run build`.",
            200,
        )

    @app.route("/<path:filename>")
    def static_assets(filename):
        if dist_dir.exists():
            return send_from_directory(dist_dir, filename)
        return jsonify({"error": "not_built"}), 404

    @app.route("/api/state")
    def api_state():
        return jsonify(host_ref.get_state())

    @app.route("/api/setup/status")
    def api_setup_status():
        # Mirrors ui/panels/music.py's SetupOverlay gate (MainWindow._ready,
        # ui/__init__.py:280-282) — first-run onboarding until a Gemini key
        # is on disk.
        return jsonify({"ready": host_ref._has_api_key()})

    @app.route("/api/setup/complete", methods=["POST"])
    def api_setup_complete():
        data = request.get_json(silent=True) or {}
        key = str(data.get("gemini_api_key", "")).strip()
        os_system = str(data.get("os_system", "")).strip() or "linux"
        if not key:
            return jsonify({"error": "missing_key"}), 400
        from actions.webui_host import API_FILE
        API_FILE.parent.mkdir(parents=True, exist_ok=True)
        # ui/__init__.py's _on_setup_done overwrites the file wholesale here
        # (safe there since first-run means the file doesn't exist yet) —
        # merge instead, since this endpoint could in principle be hit after
        # other API keys (TMDB, CardTrader, ...) already exist.
        try:
            existing = json.loads(API_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        existing.update({"gemini_api_key": key, "os_system": os_system})
        API_FILE.write_text(json.dumps(existing, indent=4, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True})

    @app.route("/api/log")
    def api_log():
        try:
            since = int(request.args.get("since", "0"))
        except ValueError:
            since = 0
        with _log_buffer_lock:
            items = [e for e in _log_buffer if e["id"] > since]
        return jsonify(items)

    @app.route("/api/command", methods=["POST"])
    def api_command():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify({"error": "empty_text"}), 400

        jarvis = _jarvis_ref
        loop = getattr(jarvis, "_loop", None)
        session = getattr(jarvis, "session", None)
        if jarvis is None or loop is None or session is None:
            return jsonify({"error": "not_ready"}), 503

        publish("log", {"text": f"Tú: {text}"})

        import asyncio
        asyncio.run_coroutine_threadsafe(jarvis._send_text_command(text), loop)
        return jsonify({"ok": True}), 202

    @app.route("/api/mic/toggle", methods=["POST"])
    def api_mic_toggle():
        host_ref.muted = not host_ref.muted
        # Mirrors ui/__init__.py's _toggle_mute: muting is a local UI action,
        # not something the voice loop pushes on its own, so the state pill
        # needs an explicit nudge here to reflect it immediately.
        host_ref.set_state("MUTED" if host_ref.muted else "LISTENING")
        return jsonify({"muted": host_ref.muted})

    @app.route("/api/interrupt", methods=["POST"])
    def api_interrupt():
        # Esc in the Qt UI (ui/__init__.py:293,454-459) cuts JARVIS off
        # mid-speech immediately.
        try:
            host_ref.fire_interrupt()
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True})

    @app.route("/api/window/fullscreen", methods=["POST"])
    def api_window_fullscreen():
        try:
            host_ref.toggle_fullscreen()
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True})

    @app.route("/api/window/always_on_top", methods=["POST"])
    def api_window_always_on_top():
        data = request.get_json(silent=True) or {}
        on_top = bool(data.get("enabled"))
        app_settings.set("ui_always_on_top", on_top)
        try:
            host_ref.set_always_on_top(on_top)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "enabled": on_top})

    @app.route("/api/events")
    def api_events():
        return Response(_sse_stream(), mimetype="text/event-stream")

    @app.route("/api/whatsapp/chats")
    def api_whatsapp_chats():
        from actions import whatsapp
        try:
            chats = whatsapp.list_recent_chats(
                limit=100, timeout=10, include_pictures=False, raise_on_unready=True,
            )
            return jsonify(chats or [])
        except whatsapp.WhatsAppUnavailable:
            return jsonify({"error": "not_ready"}), 503
        except Exception as e:
            print(f"[WebUIServer] list_recent_chats failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/messages")
    def api_whatsapp_messages():
        chat_id = (request.args.get("chat_id") or "").strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        try:
            from actions import whatsapp
            msgs = whatsapp.get_conversation(chat_id, limit=50, timeout=25, strict=True)
            return jsonify(msgs or [])
        except Exception as e:
            print(f"[WebUIServer] get_conversation failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/send", methods=["POST"])
    def api_whatsapp_send():
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
            print(f"[WebUIServer] send_whatsapp failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/avatar")
    def api_whatsapp_avatar():
        chat_id = (request.args.get("chat_id") or "").strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        from actions import whatsapp
        return jsonify({"url": whatsapp.get_profile_picture_url(chat_id)})

    @app.route("/api/whatsapp/avatar_image")
    def api_whatsapp_avatar_image():
        # An <img src> pointed straight at the CDN URL from /api/whatsapp/avatar
        # doesn't load — same reason message media is proxied through
        # /api/whatsapp/media instead of hotlinked: the bridge session is what
        # can actually reach it. Proxies bytes the same way, with caching
        # since a contact's photo rarely changes (ui/panels' own avatar cache
        # does the same, just in-process instead of via HTTP headers).
        chat_id = (request.args.get("chat_id") or "").strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        try:
            import requests as _requests
            from actions import whatsapp
            url = whatsapp.get_profile_picture_url(chat_id)
            if not url:
                return jsonify({"error": "no_avatar"}), 404
            resp = _requests.get(url, timeout=8)
            resp.raise_for_status()
            from flask import Response as _Response
            return _Response(
                resp.content,
                mimetype=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    @app.route("/api/whatsapp/status")
    def api_whatsapp_status():
        # Drives the QR-linking screen (actions/whatsapp_ui.py:1256-1338):
        # while state != "ready" the panel shows the QR instead of chats.
        from actions import whatsapp
        return jsonify(whatsapp._bridge_qr_status())

    @app.route("/api/whatsapp/reconnect", methods=["POST"])
    def api_whatsapp_reconnect():
        from actions import whatsapp
        return jsonify({"ok": whatsapp.bridge_reconnect()})

    @app.route("/api/whatsapp/send_media", methods=["POST"])
    def api_whatsapp_send_media():
        to = (request.form.get("chat_id") or "").strip()
        caption = request.form.get("caption") or ""
        f = request.files.get("file")
        if not to or not f or not f.filename:
            return jsonify({"error": "missing_fields"}), 400
        try:
            import tempfile
            from pathlib import Path as _Path
            with tempfile.TemporaryDirectory() as tmp:
                dest = _Path(tmp) / f.filename
                f.save(dest)
                from actions import whatsapp
                result = whatsapp.send_whatsapp_media(to, str(dest), caption)
                return jsonify({"ok": True, "result": result})
        except Exception as e:
            print(f"[WebUIServer] send_whatsapp_media failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/media")
    def api_whatsapp_media():
        # Proxies media bytes for an already-fetched message (the frontend
        # already has the message dict from /api/whatsapp/messages — this
        # just resolves and streams its mediaUrl, same call
        # download_message_media() makes internally).
        raw = (request.args.get("media_url") or "").strip()
        if not raw:
            return jsonify({"error": "missing_media_url"}), 400
        try:
            import requests as _requests
            from actions import whatsapp
            resp = _requests.get(whatsapp.media_url(raw), timeout=15)
            resp.raise_for_status()
            from flask import Response as _Response
            return _Response(resp.content, mimetype=resp.headers.get("content-type", "application/octet-stream"))
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    @app.route("/api/whatsapp/transcribe", methods=["POST"])
    def api_whatsapp_transcribe():
        data = request.get_json(silent=True) or {}
        message = data.get("message") or {}
        try:
            from actions import whatsapp
            text = whatsapp.transcribe_message_audio(message)
            return jsonify({"text": text})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/edit", methods=["POST"])
    def api_whatsapp_edit():
        data = request.get_json(silent=True) or {}
        try:
            from actions import whatsapp
            return jsonify(whatsapp.edit_whatsapp_message(
                str(data.get("message_id", "")), str(data.get("new_body", "")),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/mark_read", methods=["POST"])
    def api_whatsapp_mark_read():
        data = request.get_json(silent=True) or {}
        try:
            from actions import whatsapp
            return jsonify({"ok": whatsapp.mark_chat_read(str(data.get("chat_id", "")))})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/acks", methods=["POST"])
    def api_whatsapp_acks():
        data = request.get_json(silent=True) or {}
        try:
            from actions import whatsapp
            return jsonify(whatsapp.get_message_acks(data.get("message_ids") or []))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/whatsapp/suggest", methods=["POST"])
    def api_whatsapp_suggest():
        data = request.get_json(silent=True) or {}
        chat_id = str(data.get("chat_id", "")).strip()
        if not chat_id:
            return jsonify({"error": "missing_chat_id"}), 400
        try:
            from actions import whatsapp_ai
            draft = whatsapp_ai.generate_whatsapp_reply(
                chat_id, incoming_body=data.get("incoming_body", ""),
            )
            return jsonify({"draft": draft})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -- File drop zone --------------------------------------------------------
    # Mirrors ui/__init__.py's FileDropZone -> _on_file_selected (:1737):
    # a dropped/picked file becomes a synthetic [FILE_UPLOADED] text command
    # fed through the exact same on_text_command path as typing in the chat
    # box. Unlike Qt (which just keeps the original local path), the browser
    # only hands over bytes, so this saves them under DATA_DIR/uploads first.

    def _fmt_size(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 ** 2:
            return f"{n / 1024:.1f} KB"
        if n < 1024 ** 3:
            return f"{n / 1024 ** 2:.1f} MB"
        return f"{n / 1024 ** 3:.1f} GB"

    @app.route("/api/files/upload", methods=["POST"])
    def api_files_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "missing_file"}), 400

        jarvis = _jarvis_ref
        loop = getattr(jarvis, "_loop", None)
        session = getattr(jarvis, "session", None)
        if jarvis is None or loop is None or session is None:
            return jsonify({"error": "not_ready"}), 503

        from actions.paths import DATA_DIR
        upload_dir = DATA_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / f.filename
        if dest.exists():
            import time as _time
            dest = upload_dir / f"{dest.stem}_{int(_time.time())}{dest.suffix}"
        f.save(dest)

        size = _fmt_size(dest.stat().st_size)
        msg = (
            f"[FILE_UPLOADED] path={dest} | name={dest.name} | "
            f"type={dest.suffix.lstrip('.')} | size={size} | "
            f"Briefly tell the user you can see the file '{dest.name}' "
            f"({size}) has been uploaded and ask what they'd like to do with it."
        )
        publish("log", {"text": f"FILE: {dest.name} ({size}) loaded"})

        import asyncio
        asyncio.run_coroutine_threadsafe(jarvis._send_text_command(msg), loop)
        return jsonify({"ok": True, "name": dest.name, "size": size})

    # -- Downloads -------------------------------------------------------------
    # Mirrors ui/widgets/downloads.py's DownloadsListWidget row menu
    # (Pausar/Reanudar/Abrir carpeta/Borrar). Live updates ride the same
    # "downloads" SSE event WebUIHost's manager listener publishes.

    @app.route("/api/downloads")
    def api_downloads_list():
        from actions.download_manager import get_manager
        return jsonify([d.to_dict() for d in get_manager().list()])

    @app.route("/api/downloads/<download_id>/pause", methods=["POST"])
    def api_downloads_pause(download_id):
        from actions.download_manager import get_manager
        get_manager().pause(download_id)
        return jsonify({"ok": True})

    @app.route("/api/downloads/<download_id>/resume", methods=["POST"])
    def api_downloads_resume(download_id):
        from actions.download_manager import get_manager
        get_manager().resume(download_id)
        return jsonify({"ok": True})

    @app.route("/api/downloads/<download_id>/remove", methods=["POST"])
    def api_downloads_remove(download_id):
        data = request.get_json(silent=True) or {}
        from actions.download_manager import get_manager
        get_manager().remove(download_id, delete_files=bool(data.get("delete_files", False)))
        return jsonify({"ok": True})

    @app.route("/api/downloads/<download_id>/open_folder", methods=["POST"])
    def api_downloads_open_folder(download_id):
        # Runs on the same machine as the browser view, so this genuinely
        # opens a real file-manager window on the user's desktop — same
        # effect as the Qt panel's action, not a no-op in a web context.
        from actions.download_manager import get_manager
        get_manager().open_folder(download_id)
        return jsonify({"ok": True})

    # -- Calendar ------------------------------------------------------------
    # Mirrors ui/panels/calendar.py exactly: single "primary" calendar, a
    # month-range fetch, create/update/delete, and search. No recurrence,
    # reminders, all-day authoring or multi-calendar picker exist in the Qt
    # panel either — this is a faithful port, not new scope.

    @app.route("/api/calendar/events")
    def api_calendar_events():
        time_min = (request.args.get("time_min") or "").strip()
        time_max = (request.args.get("time_max") or "").strip()
        if not time_min or not time_max:
            return jsonify({"error": "missing_range"}), 400
        try:
            from actions import google_calendar as gcal
            return jsonify(gcal.list_events_range(time_min, time_max) or [])
        except Exception as e:
            return _google_error(e)

    @app.route("/api/calendar/search")
    def api_calendar_search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "empty_query"}), 400
        try:
            from actions import google_calendar as gcal
            return jsonify(gcal.search_events(q, max_results=30) or [])
        except Exception as e:
            return _google_error(e)

    @app.route("/api/calendar/create", methods=["POST"])
    def api_calendar_create():
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
                attendees=data.get("attendees") or None,
            )
            return jsonify({"ok": True, "event": created})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/calendar/update", methods=["POST"])
    def api_calendar_update():
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
                attendees=data.get("attendees") or [],
            )
            return jsonify({"ok": True, "event": updated})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/calendar/delete", methods=["POST"])
    def api_calendar_delete():
        data = request.get_json(silent=True) or {}
        event_id = str(data.get("event_id", "")).strip()
        if not event_id:
            return jsonify({"error": "missing_event_id"}), 400
        try:
            from actions import google_calendar as gcal
            return jsonify({"ok": True, "result": gcal.delete_event(event_id)})
        except Exception as e:
            return _google_error(e)

    # -- Contacts (calendar's attendee picker) --------------------------------

    @app.route("/api/contacts")
    def api_contacts():
        from actions import contacts
        return jsonify(contacts.list_contacts() or [])

    @app.route("/api/contacts", methods=["POST"])
    def api_contacts_upsert():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip()
        from actions import contacts
        if not email or not contacts.is_valid_email(email):
            return jsonify({"error": "invalid_email"}), 400
        return jsonify(contacts.upsert_contact(name, email))

    # -- Gmail -----------------------------------------------------------------
    # Mirrors ui/panels/gmail.py: paginated get_email_page for listing/search/
    # folders, read_email for the detail view, archive/trash, and send (with
    # attachments). No reply/forward/star/labels in the Qt panel either.

    @app.route("/api/gmail/emails")
    def api_gmail_emails():
        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "50"))
        except ValueError:
            return jsonify({"error": "bad_pagination"}), 400
        label = request.args.get("label", "ALL")
        unread_only = request.args.get("unread_only", "").lower() in ("1", "true")
        query = request.args.get("query", "")
        try:
            from actions import gmail
            return jsonify(gmail.get_email_page(
                page=page, page_size=page_size, label=label,
                unread_only=unread_only, query=query,
            ))
        except Exception as e:
            return _google_error(e)

    @app.route("/api/gmail/email/<email_id>")
    def api_gmail_read(email_id):
        try:
            from actions import gmail
            return jsonify(gmail.read_email(email_id))
        except Exception as e:
            return _google_error(e)

    @app.route("/api/gmail/email/<email_id>/images")
    def api_gmail_images(email_id):
        try:
            from actions import gmail
            return jsonify(gmail.read_email_images(email_id))
        except Exception as e:
            return _google_error(e)

    @app.route("/api/gmail/email/<email_id>/archive", methods=["POST"])
    def api_gmail_archive(email_id):
        try:
            from actions import gmail
            gmail.archive_email(email_id)
            return jsonify({"ok": True})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/gmail/email/<email_id>/trash", methods=["POST"])
    def api_gmail_trash(email_id):
        try:
            from actions import gmail
            gmail.trash_email(email_id)
            return jsonify({"ok": True})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/gmail/ai_draft", methods=["POST"])
    def api_gmail_ai_draft():
        data = request.get_json(silent=True) or {}
        instruction = str(data.get("instruction", "")).strip()
        if not instruction:
            return jsonify({"error": "empty_instruction"}), 400
        try:
            from actions import gmail_ai
            return jsonify(gmail_ai.draft_email(
                instruction, to=data.get("to", ""), subject=data.get("subject", ""),
            ))
        except Exception as e:
            return _google_error(e)

    @app.route("/api/gmail/send", methods=["POST"])
    def api_gmail_send():
        # multipart/form-data so the compose dialog's attach-files can ride
        # along in the same request, the way GmailComposeDialog's local file
        # picker feeds send_email(attachments=[...]) directly.
        to = (request.form.get("to") or "").strip()
        subject = request.form.get("subject") or ""
        body = request.form.get("body") or ""
        if not to:
            return jsonify({"error": "missing_to"}), 400
        tmp_paths = []
        try:
            import tempfile
            from pathlib import Path
            upload_dir = Path(tempfile.mkdtemp(prefix="jarvis_gmail_"))
            for f in request.files.getlist("attachments"):
                if not f.filename:
                    continue
                dest = upload_dir / f.filename
                f.save(dest)
                tmp_paths.append(str(dest))
            from actions import gmail
            result = gmail.send_email(to, subject, body, attachments=tmp_paths or None)
            return jsonify({"ok": True, "result": result})
        except Exception as e:
            return _google_error(e)
        finally:
            for p in tmp_paths:
                try:
                    import os
                    os.remove(p)
                except OSError:
                    pass

    # -- Drive -------------------------------------------------------------
    # Mirrors ui/panels/drive.py: folder browse, search, preview, upload,
    # download, share (reader-only in the Qt panel — role is exposed here
    # since the backend already supports it and it's zero extra risk),
    # rename, and trash-delete. create_folder/update_file exist server-side
    # but have no button in the Qt panel; included here too since they're
    # already fully implemented and cost nothing to expose.

    @app.route("/api/drive/files")
    def api_drive_files():
        folder_id = request.args.get("folder_id") or None
        try:
            count = int(request.args.get("count", "200"))
        except ValueError:
            count = 200
        try:
            from actions import gdrive
            return jsonify(gdrive.list_files(count=count, folder_id=folder_id) or [])
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/search")
    def api_drive_search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "empty_query"}), 400
        folder_id = request.args.get("folder_id") or None
        try:
            from actions import gdrive
            return jsonify(gdrive.search_files(q, folder_id=folder_id) or [])
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/preview/<file_id>")
    def api_drive_preview(file_id):
        # get_file_preview()'s "data" field is raw bytes — not JSON
        # serializable, and pointless to inline as base64 for anything but
        # tiny text files. Text previews are decoded and returned inline;
        # binary kinds (image/audio/pdf) are described here and fetched
        # separately from /api/drive/preview_raw as a real HTTP response
        # with the right Content-Type, so the frontend can point an
        # <img>/<audio>/<iframe> straight at it.
        try:
            from actions import gdrive
            result = gdrive.get_file_preview(file_id)
        except Exception as e:
            return _google_error(e)
        kind = result.get("kind")
        out = {
            "kind": kind,
            "name": result.get("name") or (result.get("info") or {}).get("name"),
            "mimeType": result.get("mimeType"),
            "size": result.get("size"),
            "video": result.get("video", False),
        }
        if kind == "text":
            data = result.get("data") or b""
            out["text"] = data.decode("utf-8", errors="replace")[:120_000]
        return jsonify(out)

    @app.route("/api/drive/preview_raw/<file_id>")
    def api_drive_preview_raw(file_id):
        try:
            from actions import gdrive
            result = gdrive.get_file_preview(file_id)
        except Exception as e:
            return _google_error(e)
        if result.get("kind") not in ("image", "audio", "pdf"):
            return jsonify({"error": "not_binary_preview"}), 400
        from flask import Response as _Response
        return _Response(result.get("data") or b"", mimetype=result.get("mimeType") or "application/octet-stream")

    @app.route("/api/drive/download/<file_id>")
    def api_drive_download(file_id):
        try:
            import tempfile
            from actions import gdrive
            with tempfile.TemporaryDirectory() as tmp:
                result = gdrive.download_file(file_id=file_id, output_dir=tmp)
                path = result.get("path") if isinstance(result, dict) else None
                if not path:
                    return jsonify({"error": "download_failed"}), 500
                from flask import send_file
                return send_file(path, as_attachment=True, download_name=Path(path).name)
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/upload", methods=["POST"])
    def api_drive_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "missing_file"}), 400
        folder_id = request.form.get("folder_id") or None
        try:
            import tempfile
            from pathlib import Path as _Path
            with tempfile.TemporaryDirectory() as tmp:
                dest = _Path(tmp) / f.filename
                f.save(dest)
                from actions import gdrive
                result = gdrive.upload_file(str(dest), folder_id=folder_id)
                return jsonify({"ok": True, "file": result})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/create_folder", methods=["POST"])
    def api_drive_create_folder():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        if not name:
            return jsonify({"error": "missing_name"}), 400
        try:
            from actions import gdrive
            return jsonify({"ok": True, "folder": gdrive.create_folder(
                name, parent_id=data.get("parent_id") or None,
            )})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/share", methods=["POST"])
    def api_drive_share():
        data = request.get_json(silent=True) or {}
        file_id = str(data.get("file_id", "")).strip()
        if not file_id:
            return jsonify({"error": "missing_file_id"}), 400
        try:
            from actions import gdrive
            result = gdrive.share_file(
                file_id=file_id,
                email=str(data.get("email", "")),
                role=str(data.get("role", "reader")),
                anyone=bool(data.get("anyone", False)),
                notify=bool(data.get("notify", False)),
            )
            return jsonify({"ok": True, "result": result})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/rename", methods=["POST"])
    def api_drive_rename():
        data = request.get_json(silent=True) or {}
        file_id = str(data.get("file_id", "")).strip()
        new_name = str(data.get("new_name", "")).strip()
        if not file_id or not new_name:
            return jsonify({"error": "missing_fields"}), 400
        try:
            from actions import gdrive
            return jsonify({"ok": True, "file": gdrive.rename_file(file_id=file_id, new_name=new_name)})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/drive/delete", methods=["POST"])
    def api_drive_delete():
        data = request.get_json(silent=True) or {}
        file_id = str(data.get("file_id", "")).strip()
        if not file_id:
            return jsonify({"error": "missing_file_id"}), 400
        try:
            from actions import gdrive
            result = gdrive.delete_file(file_id=file_id, permanent=bool(data.get("permanent", False)))
            return jsonify({"ok": True, "result": result})
        except Exception as e:
            return _google_error(e)

    @app.route("/api/music/playlists")
    def api_music_playlists():
        try:
            from actions import ytmusic
            return jsonify(ytmusic.list_playlists(limit=None) or [])
        except Exception as e:
            print(f"[WebUIServer] list_playlists failed: {e}")
            return jsonify([])

    @app.route("/api/music/recommendations")
    def api_music_recommendations():
        try:
            from actions import ytmusic
            return jsonify(ytmusic.get_recommendations(limit=60) or [])
        except Exception as e:
            print(f"[WebUIServer] get_recommendations failed: {e}")
            return jsonify([])

    @app.route("/api/music/playlist_tracks")
    def api_music_playlist_tracks():
        playlist_id = (request.args.get("playlist_id") or "").strip()
        if not playlist_id:
            return jsonify({"error": "missing_playlist_id"}), 400
        # limit<=0 means "every track". The Qt panel renders a fast first
        # page and then swaps in the full list (ui/panels/music.py's
        # _PLAYLIST_FIRST_PAGE); a fixed cap here silently truncated large
        # playlists — a 1463-track library stopped dead at 200.
        try:
            raw_limit = int(request.args.get("limit", "100"))
        except ValueError:
            raw_limit = 100
        limit = raw_limit if raw_limit > 0 else None
        try:
            from actions import ytmusic
            return jsonify(ytmusic.list_playlist_tracks(playlist_id, limit=limit) or [])
        except Exception as e:
            print(f"[WebUIServer] list_playlist_tracks failed: {e}")
            return jsonify([])

    @app.route("/api/music/lyrics")
    def api_music_lyrics():
        """Time-stamped lyrics as [{time, line}] for the karaoke toggle.
        An empty list just means no provider had synced lyrics."""
        title = (request.args.get("title") or "").strip()
        artists = (request.args.get("artists") or "").strip()
        if not title:
            return jsonify({"error": "missing_title"}), 400
        try:
            from actions.lyrics import get_synced_lyrics
            lines = get_synced_lyrics(title, artists) or ()
            return jsonify([{"time": float(t), "line": str(line)} for t, line in lines])
        except Exception as e:
            print(f"[WebUIServer] lyrics failed: {e}")
            return jsonify([])

    @app.route("/api/music/search")
    def api_music_search():
        # Mirrors ui/panels/music.py's search(): songs/playlists/artists are
        # all searched at once, and the frontend's filter tabs just pick
        # which array to show — matches the real app's flow instead of a
        # single songs-only query.
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "empty_query"}), 400
        try:
            from actions import ytmusic
            return jsonify({
                "songs": ytmusic.search_songs(q, limit=40) or [],
                "playlists": ytmusic.search_playlists(q, limit=40) or [],
                "artists": ytmusic.search_artists(q, limit=30) or [],
            })
        except Exception as e:
            print(f"[WebUIServer] search failed: {e}")
            return jsonify({"songs": [], "playlists": [], "artists": []})

    @app.route("/api/music/queue")
    def api_music_queue():
        try:
            limit = int(request.args.get("limit", "300"))
        except ValueError:
            limit = 300
        try:
            from actions import ytmusic_headless
            return jsonify(ytmusic_headless.queue_snapshot(limit=limit))
        except Exception as e:
            print(f"[WebUIServer] queue_snapshot failed: {e}")
            return jsonify([])

    @app.route("/api/music/<action>", methods=["POST"])
    def api_music_action(action):
        if action not in _MUSIC_ACTIONS:
            return jsonify({"error": "unknown_action"}), 400
        ctrl = getattr(host_ref, "playback_controller", None)
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
            return jsonify({"ok": True, "pending": True}), 202
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "result": str(result)})

    # -- CardTrader --------------------------------------------------------
    # Mirrors ui/panels/cardtrader.py's three tabs (Buscar/Mazo/Carrito).
    # Simplification vs the Qt panel: search/quote progress isn't streamed
    # incrementally (no progress_cb wiring over HTTP) — the request just
    # blocks until done and the frontend shows a spinner. Everything else
    # (filters, comparison mode, deck parsing, cart ops) is the same calls.

    def _offer_filters(data: dict):
        from actions.cardtrader_optimizer import OfferFilters
        lang = data.get("language") or ""
        return OfferFilters(
            zero_only=bool(data.get("zero_only", True)),
            min_condition=str(data.get("min_condition") or "Moderately Played"),
            languages=None if not lang or lang == "Cualquiera" else [lang],
            foil=data.get("foil"),  # None | True | False
            max_printings=25 if data.get("fast") else None,
        )

    @app.route("/api/cardtrader/status")
    def api_cardtrader_status():
        try:
            from actions import cardtrader_catalog
            return jsonify(cardtrader_catalog.catalog_status())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cardtrader/sync", methods=["POST"])
    def api_cardtrader_sync():
        try:
            from actions import cardtrader_catalog
            return jsonify(cardtrader_catalog.ensure_catalog())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cardtrader/search", methods=["POST"])
    def api_cardtrader_search():
        import dataclasses
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        if not name:
            return jsonify({"error": "empty_name"}), 400
        filters = _offer_filters(data.get("filters") or {})
        try:
            from actions.cardtrader_catalog import find_blueprints
            from actions.cardtrader_optimizer import best_offer, compare_versions
            from actions.cardtrader_api import CardTraderClient
            client = CardTraderClient()
            if data.get("all_versions"):
                versions = compare_versions(name, filters, client=client)
                return jsonify({"kind": "versions", "data": [dataclasses.asdict(v) for v in versions]})
            blueprints = find_blueprints(name)
            if not blueprints:
                return jsonify({"kind": "not_found", "data": name})
            result = best_offer(blueprints, qty=1, filters=filters, client=client)
            return jsonify({"kind": "offer", "data": dataclasses.asdict(result) if result else None})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cardtrader/quote_deck", methods=["POST"])
    def api_cardtrader_quote_deck():
        import dataclasses
        data = request.get_json(silent=True) or {}
        text = str(data.get("deck_text", ""))
        if not text.strip():
            return jsonify({"error": "empty_deck"}), 400
        try:
            from actions.deck_parser import parse_deck_text
            from actions.cardtrader_optimizer import quote_deck
            parsed = parse_deck_text(text)
            if not parsed.entries:
                return jsonify({"error": "no_entries_parsed", "unparsed": parsed.unparsed}), 400
            filters = _offer_filters(data.get("filters") or {})
            quote = quote_deck(
                parsed.entries, filters,
                respect_printings=bool(data.get("respect_printings", False)),
            )
            result = dataclasses.asdict(quote)
            result["unparsed"] = parsed.unparsed
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cardtrader/cart_add", methods=["POST"])
    def api_cardtrader_cart_add():
        data = request.get_json(silent=True) or {}
        items = data.get("items") or []
        try:
            from actions.cardtrader_api import CardTraderClient, CardTraderError
            client = CardTraderClient()
            failures = []
            for item in items:
                try:
                    client.cart_add(int(item["product_id"]), int(item["quantity"]))
                except CardTraderError as e:
                    failures.append({"product_id": item.get("product_id"), "error": str(e)})
            return jsonify({"ok": True, "added": len(items) - len(failures), "failures": failures})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cardtrader/cart")
    def api_cardtrader_cart():
        try:
            from actions.cardtrader_api import CardTraderClient
            return jsonify(CardTraderClient().cart())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cardtrader/cart_remove", methods=["POST"])
    def api_cardtrader_cart_remove():
        data = request.get_json(silent=True) or {}
        try:
            from actions.cardtrader_api import CardTraderClient
            client = CardTraderClient()
            client.cart_remove(int(data["product_id"]), int(data["quantity"]))
            return jsonify({"ok": True, "cart": client.cart()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -- Movies / TV -----------------------------------------------------------
    # Mirrors ui/panels/movies.py's MoviesModePanel (TV is the same panel with
    # media_type="tv" — season/episode become extra query params, not a
    # separate flow): search/trending via movie_search.py (TMDB), then
    # torrentio.search() for streamable sources once an IMDb id is resolved,
    # then vlc_player.start_streaming() for the actual HTTP stream URL a
    # <video> tag can play directly.

    def _movie_error(exc: Exception):
        from actions.movie_search import MovieSearchError
        if isinstance(exc, MovieSearchError) and "API key" in str(exc):
            return jsonify({"error": str(exc), "needs_api_key": True}), 503
        return jsonify({"error": str(exc)}), 500

    @app.route("/api/movies/search")
    def api_movies_search():
        q = (request.args.get("q") or "").strip()
        kind = request.args.get("kind") or "multi"
        if not q:
            return jsonify({"error": "missing_query"}), 400
        try:
            from actions import movie_search
            results = movie_search.search(q, kind=kind, limit=20)
            return jsonify([m.to_dict() for m in results])
        except Exception as e:
            return _movie_error(e)

    @app.route("/api/movies/trending")
    def api_movies_trending():
        kind = request.args.get("kind") or "movie"
        try:
            from actions import movie_search
            results = movie_search.get_trending(kind=kind, limit=20)
            return jsonify([m.to_dict() for m in results])
        except Exception as e:
            return _movie_error(e)

    @app.route("/api/movies/details/<int:tmdb_id>")
    def api_movies_details(tmdb_id):
        kind = request.args.get("kind") or "movie"
        try:
            from actions import movie_search
            movie = movie_search.get_details(tmdb_id, kind=kind)
            if not movie:
                return jsonify({"error": "not_found"}), 404
            return jsonify(movie.to_dict())
        except Exception as e:
            return _movie_error(e)

    @app.route("/api/movies/torrents")
    def api_movies_torrents():
        tmdb_id = request.args.get("tmdb_id", type=int)
        kind = request.args.get("kind") or "movie"
        season = request.args.get("season", type=int) or 0
        episode = request.args.get("episode", type=int) or 0
        if not tmdb_id:
            return jsonify({"error": "missing_tmdb_id"}), 400
        try:
            from actions import movie_search, torrentio
            imdb_id = movie_search.get_imdb_id(tmdb_id, kind=kind)
            if not imdb_id:
                return jsonify({"error": "No se encontró un identificador IMDb para este título."}), 404
            streams = torrentio.search(imdb_id, kind=kind, season=season, episode=episode)
            return jsonify([s.to_dict() for s in streams])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/movies/play", methods=["POST"])
    def api_movies_play():
        data = request.get_json(silent=True) or {}
        magnet = str(data.get("magnet") or "").strip()
        file_idx = data.get("file_idx", -1)
        if not magnet:
            return jsonify({"error": "missing_magnet"}), 400

        def _run():
            from actions import vlc_player
            def _progress(peers, elapsed):
                publish("stream_progress", {"peers": peers, "elapsed": elapsed})
            try:
                url = vlc_player.start_streaming(magnet, file_index=file_idx, progress_hook=_progress)
                publish("stream_ready", {"url": url})
            except Exception as e:
                publish("stream_error", {"error": str(e)})

        # start_streaming blocks 1-65s waiting for swarm metadata (real
        # trackers, not a bug) — same reason the Qt panel shows a live
        # "buscando fuente… N peers, Ys" instead of a plain spinner. The SSE
        # events above are that same feedback for the web.
        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True}), 202

    @app.route("/api/movies/stop", methods=["POST"])
    def api_movies_stop():
        try:
            from actions import vlc_player
            vlc_player.stop_streaming()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/movies/status")
    def api_movies_status():
        from actions import vlc_player
        status = vlc_player.get_status()
        return jsonify({"ready": status is not None, "status": status})

    # -- Anime -----------------------------------------------------------------
    # ui/panels/movies.py's AnimeModePanel subclasses MoviesModePanel and plays
    # through the exact same vlc_player pipeline — only the metadata source and
    # the torrent lookup (NyaaSi via torrentio.search_anime, no providers
    # filter) differ, so playback reuses /api/movies/play|stop|status as-is.

    @app.route("/api/anime/search")
    def api_anime_search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "missing_query"}), 400
        try:
            from actions import movie_search
            results = movie_search.search_anime(q, limit=20)
            return jsonify([m.to_dict() for m in results])
        except Exception as e:
            return _movie_error(e)

    @app.route("/api/anime/trending")
    def api_anime_trending():
        airing = request.args.get("airing") == "1"
        try:
            from actions import movie_search
            fn = movie_search.get_airing_anime if airing else movie_search.get_trending_anime
            results = fn(limit=20)
            return jsonify([m.to_dict() for m in results])
        except Exception as e:
            return _movie_error(e)

    @app.route("/api/anime/torrents")
    def api_anime_torrents():
        tmdb_id = request.args.get("tmdb_id", type=int)
        season = request.args.get("season", type=int) or 0
        episode = request.args.get("episode", type=int) or 0
        if not tmdb_id:
            return jsonify({"error": "missing_tmdb_id"}), 400
        try:
            from actions import movie_search, torrentio
            imdb_id = movie_search.get_imdb_id(tmdb_id, kind="tv")
            if not imdb_id:
                return jsonify({"error": "No se encontró un identificador IMDb para este título."}), 404
            streams = torrentio.search_anime(imdb_id, kind="tv", season=season, episode=episode)
            return jsonify([s.to_dict() for s in streams])
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -- YouTube -----------------------------------------------------------
    # ui/panels/youtube.py's EmbeddedVideoPlayer hands the watch URL straight
    # to mpv and lets mpv's own ytdl_hook resolve + demux the stream
    # internally. A browser <video> tag can't do that — verified live against
    # a real video that YouTube now serves with zero progressive (pre-muxed)
    # formats, so there is no single URL to hand it. The YouTube iframe embed
    # (youtube.com/embed/<id>) is what the rest of the web uses for exactly
    # this reason; playback needs no backend route at all, only search/
    # recommendations for the browse UI.

    @app.route("/api/youtube/search")
    def api_youtube_search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "missing_query"}), 400
        from actions import youtube_player
        return jsonify(youtube_player.search_videos(q, limit=24))

    @app.route("/api/youtube/recommendations")
    def api_youtube_recommendations():
        from actions import youtube_player
        videos = youtube_player.fetch_home_recommendations(limit=24)
        return jsonify(videos)

    # -- Settings ------------------------------------------------------------
    # Mirrors ui/panels/settings.py's 13 tabs. Most toggles/combos are plain
    # actions.app_settings keys (same store the Qt panel writes to), exposed
    # here as one generic get/set pair rather than one route per key. The
    # handful of settings that live elsewhere (API keys file, long-term
    # memory profile, WhatsApp auto-reply rules, anime SFW flag, audio
    # device enumeration, factory reset) each get their own route, matching
    # exactly where settings.py itself reads/writes them.

    @app.route("/api/settings")
    def api_settings_get():
        return jsonify(app_settings.all_settings())

    @app.route("/api/settings", methods=["POST"])
    def api_settings_set():
        data = request.get_json(silent=True) or {}
        key = str(data.get("key", "")).strip()
        if not key or "value" not in data:
            return jsonify({"error": "missing_fields"}), 400
        app_settings.set(key, data["value"])
        return jsonify({"ok": True})

    @app.route("/api/settings/whatsapp/toggle", methods=["POST"])
    def api_settings_whatsapp_toggle():
        data = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled"))
        app_settings.set("whatsapp_enabled", enabled)

        def _apply():
            try:
                if enabled:
                    from actions.whatsapp_bridge_process import start_bridge
                    start_bridge()
                else:
                    from actions.whatsapp_bridge_process import stop_bridge
                    stop_bridge()
            except Exception as e:
                print(f"[WebUIServer] whatsapp bridge toggle failed: {e}")

        threading.Thread(target=_apply, daemon=True).start()
        return jsonify({"ok": True, "enabled": enabled})

    @app.route("/api/settings/whatsapp_rules")
    def api_settings_wa_rules():
        from actions import whatsapp_rules
        return jsonify(whatsapp_rules.load_rules())

    @app.route("/api/settings/whatsapp_rules", methods=["POST"])
    def api_settings_wa_rules_add():
        from actions import whatsapp_rules
        data = request.get_json(silent=True) or {}
        return jsonify(whatsapp_rules.add_rule(data))

    @app.route("/api/settings/whatsapp_rules/<rule_id>", methods=["POST"])
    def api_settings_wa_rules_update(rule_id):
        from actions import whatsapp_rules
        data = request.get_json(silent=True) or {}
        ok = whatsapp_rules.update_rule(rule_id, data)
        return jsonify({"ok": ok})

    @app.route("/api/settings/whatsapp_rules/<rule_id>", methods=["DELETE"])
    def api_settings_wa_rules_delete(rule_id):
        from actions import whatsapp_rules
        return jsonify({"ok": whatsapp_rules.delete_rule(rule_id)})

    @app.route("/api/settings/whatsapp_rules/<rule_id>/move", methods=["POST"])
    def api_settings_wa_rules_move(rule_id):
        from actions import whatsapp_rules
        data = request.get_json(silent=True) or {}
        delta = int(data.get("delta", 0))
        return jsonify({"ok": whatsapp_rules.move_rule(rule_id, delta)})

    @app.route("/api/settings/api_keys")
    def api_settings_api_keys_get():
        try:
            from actions.paths import config_path
            return jsonify(json.loads(config_path("api_keys.json").read_text(encoding="utf-8")))
        except Exception:
            return jsonify({})

    @app.route("/api/settings/api_keys", methods=["POST"])
    def api_settings_api_keys_set():
        from actions.paths import config_path
        data = request.get_json(silent=True) or {}
        path = config_path("api_keys.json")
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        for key, val in data.items():
            val = str(val or "").strip()
            if val:
                cfg[key] = val
            else:
                cfg.pop(key, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        from actions.paths import config_path as _cp
        return jsonify({
            "ok": True,
            "has_google_credentials": _cp("google_credentials.json").is_file(),
        })

    @app.route("/api/settings/profile")
    def api_settings_profile_get():
        from memory.memory_manager import load_memory
        identity = load_memory().get("identity", {}) or {}

        def _val(key):
            entry = identity.get(key)
            return entry.get("value") if isinstance(entry, dict) else (entry or "")

        return jsonify({"name": _val("name"), "about": _val("about")})

    @app.route("/api/settings/profile", methods=["POST"])
    def api_settings_profile_set():
        from memory.memory_manager import update_memory, forget
        data = request.get_json(silent=True) or {}
        for key in ("name", "about"):
            if key not in data:
                continue
            val = str(data[key] or "").strip()
            if val:
                update_memory({"identity": {key: {"value": val}}})
            else:
                forget(key, "identity")
        return jsonify({"ok": True})

    @app.route("/api/settings/anime_sfw")
    def api_settings_anime_sfw_get():
        from actions import anime_search
        return jsonify({"sfw": anime_search.get_sfw()})

    @app.route("/api/settings/anime_sfw", methods=["POST"])
    def api_settings_anime_sfw_set():
        from actions import anime_search
        data = request.get_json(silent=True) or {}
        anime_search.set_sfw(bool(data.get("sfw")))
        return jsonify({"ok": True})

    @app.route("/api/settings/audio_devices")
    def api_settings_audio_devices():
        inputs = []
        try:
            import sounddevice as sd
            for d in sd.query_devices():
                if d.get("max_input_channels", 0) > 0:
                    inputs.append(d.get("name"))
        except Exception as e:
            print(f"[WebUIServer] audio input enumeration failed: {e}")
        outputs = []
        try:
            from actions import ytmusic_headless
            outputs = ytmusic_headless.list_audio_output_devices()
        except Exception as e:
            print(f"[WebUIServer] audio output enumeration failed: {e}")
        return jsonify({"inputs": inputs, "outputs": outputs})

    @app.route("/api/settings/performance_plan", methods=["POST"])
    def api_settings_performance_plan():
        data = request.get_json(silent=True) or {}
        plan = str(data.get("plan", "balanced"))
        app_settings.set("performance_plan", plan)
        try:
            from actions import platform_runtime
            platform_runtime.apply_performance_plan(plan)
        except Exception as e:
            print(f"[WebUIServer] apply_performance_plan failed: {e}")
        return jsonify({"ok": True})

    @app.route("/api/settings/lan_dashboard/qr")
    def api_settings_lan_dashboard_qr():
        try:
            from actions import lan_dashboard
            url = lan_dashboard.start_dashboard()
            import io
            import qrcode
            img = qrcode.make(url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            import base64
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return jsonify({"url": url, "qr_png_base64": b64})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/reset", methods=["POST"])
    def api_settings_reset():
        try:
            from actions import app_reset
            app_reset.schedule_reset_and_restart()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/about")
    def api_settings_about():
        from actions.paths import config_path
        return jsonify({
            "version": "Mark XXXIX",
            "interface": "Web (pywebview)",
            "settings_path": str(config_path("app_settings.json")),
        })

    return app


def set_jarvis(jarvis) -> None:
    """Hand over the JarvisLive instance so /api/command can forward typed
    text the same way actions/lan_dashboard.py's api_command does. Called
    from main.py once JarvisLive is constructed, mirroring
    lan_dashboard.set_jarvis(jarvis)."""
    global _jarvis_ref
    _jarvis_ref = jarvis


def start(host_ref, port: int | None = None) -> str:
    """Start the web UI backend in a daemon thread (idempotent). Returns
    the loopback base URL it's serving on."""
    global _app, _started, _host_ref

    port = port or int(app_settings.get("webui_port", 8877))
    _host_ref = host_ref

    if not _started:
        _app = _make_app(host_ref)
        threading.Thread(
            target=lambda: _app.run(
                host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True,
            ),
            daemon=True,
        ).start()
        _started = True

    return f"http://127.0.0.1:{port}/"
