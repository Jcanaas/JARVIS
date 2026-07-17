"""Gestor de mensajes WhatsApp que guarda mensajes entrantes como pendientes
y solo envía respuestas cuando el usuario las aprueba.

Uso básico:
 - Instanciar `WhatsAppManager()` (inicia un hilo de polling)
 - `list_pending()` para ver mensajes nuevos
 - `prepare_reply(message_id, text)` para crear un borrador
 - `send_reply(message_id)` para enviar el borrador (requiere que exista)
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from .whatsapp import fetch_messages, is_ignored_message, resolve_contact, send_whatsapp, transcribe_message_audio

from actions.paths import RESOURCE_DIR, DATA_DIR
ROOT = RESOURCE_DIR
PENDING_FILE = DATA_DIR / "whatsapp_pending.json"

_YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/watch\?v=|music\.youtube\.com/watch\?v=)"
    r"([A-Za-z0-9_-]{11})"
)


def _extract_youtube_id(body: str) -> str:
    match = _YOUTUBE_ID_RE.search(str(body or ""))
    return match.group(1) if match else ""


def _load_pending() -> Dict[str, Any]:
    if not PENDING_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending(data: Dict[str, Any]):
    PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class WhatsAppManager:
    def __init__(
        self,
        poll_interval: float = 1.5,
        on_new_message=None,
        reply_generator=None,
        message_sender=None,
        start_thread: bool = True,
        on_unanswered_reminder=None,
        reminder_after_secs: float = 3 * 3600,
    ):
        self.poll_interval = poll_interval
        # "Responder esto" reminder: nudge once per message if an incoming
        # message sits unanswered (not in _pending as sent) past this long.
        self.on_unanswered_reminder = on_unanswered_reminder
        self.reminder_after_secs = reminder_after_secs
        self._reminded: set = set()
        self._last_reminder_scan = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # _seen: in-session dedup set — does NOT load old file IDs.
        # This avoids stale IDs from previous sessions blocking new messages.
        self._seen: set = set()
        # _pending still used for list_pending() but not for dedup
        self._pending = _load_pending()
        # Purge entries older than 24 h so the file doesn't grow forever
        cutoff = int(time.time() * 1000) - 24 * 3600 * 1000
        self._pending = {k: v for k, v in self._pending.items()
                         if (v.get('timestamp') or 0) >= cutoff}
        # Start from NOW — only messages arriving after this point are new
        self._last_ts = int(time.time() * 1000)
        self.on_new_message = on_new_message
        self.on_auto_reply = None
        # Extra listeners notified for every new incoming message (besides
        # on_new_message). Used e.g. by the chat UI to update in real time.
        self._message_listeners: List = []
        self._reply_generator = reply_generator
        self._message_sender = message_sender or send_whatsapp
        self._auto_reply_sessions: Dict[str, Dict[str, Any]] = {}
        self._auto_reply_busy: set[str] = set()
        self._auto_reply_queues: Dict[str, List[Dict[str, Any]]] = {}
        # Listeners notified when an existing message gets edited (own or
        # remote). Separate from _message_listeners since edits aren't new
        # messages and shouldn't trigger auto-reply/transcription.
        self._edit_listeners: List = []
        if start_thread:
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()

    def _loop(self):
        import sys
        _err_count = 0
        _backoff = 1.0
        while not self._stop.is_set():
            try:
                msgs = fetch_messages(self._last_ts)
                # reset backoff on success
                if _err_count > 0:
                    print("[WA] puente reconectado.", file=sys.stderr)
                _err_count = 0
                _backoff = 1.0
                if msgs:
                    new_entries = []
                    msg_map = {}  # Map message IDs to bridge messages for audio transcription
                    newest_timestamp = self._last_ts
                    edit_entries = []
                    with self._lock:
                        for m in msgs:
                            if is_ignored_message(m):
                                continue
                            # Process all messages: incoming (need responses) + outgoing (sent from mobile).
                            # Outgoing messages from the mobile should appear in the chat UI.
                            mid = m.get('id') or f"{m.get('from')}_{m.get('timestamp')}"
                            try:
                                newest_timestamp = max(newest_timestamp, int(m.get("timestamp") or 0))
                            except (TypeError, ValueError):
                                pass
                            # Edit events reuse the original message's id (so its
                            # timestamp/order in the pending store doesn't change),
                            # which means they'd be skipped by the dedup check below
                            # if handled as normal messages. Handle them separately:
                            # patch the stored body and notify edit listeners.
                            if str(m.get('type') or '').lower() == 'edit':
                                pending_entry = self._pending.get(mid)
                                if pending_entry is not None:
                                    pending_entry['body'] = m.get('body') or ''
                                    pending_entry['edited'] = True
                                edit_entries.append({
                                    'id': mid,
                                    'from': m.get('chatId'),
                                    'body': m.get('body') or '',
                                    'edited': True,
                                })
                                continue
                            # deduplicate using session-only _seen set
                            if mid in self._seen:
                                continue
                            self._seen.add(mid)
                            from_me = bool(m.get('fromMe')) or str(m.get('direction') or '').lower() == 'out'
                            entry = {
                                'id': mid,
                                # 'from' is used throughout as "the chat this
                                # message belongs to", not "who sent it". The
                                # bridge already computes that correctly as
                                # chatId (msg.fromMe ? msg.to : msg.from); for
                                # our own outgoing messages msg.from is *our*
                                # JID, which would point every self-sent message
                                # (phone or Jarvis) at the wrong chat and make
                                # an already-open conversation never refresh.
                                'from': m.get('chatId') or m.get('from'),
                                'author': m.get('author') or None,
                                'authorName': m.get('authorName') or None,
                                'senderName': m.get('senderName') or None,
                                'body': m.get('body') or '',
                                'type': m.get('type', 'chat'),
                                'timestamp': m.get('timestamp'),
                                'fromMe': from_me,
                                'ack': m.get('ack'),
                                # Preserve media metadata so the chat UI can render
                                # the inline player/transcription for audio, images,
                                # etc. (without these the bubble shows only "[nota de
                                # voz]" and similar placeholders).
                                'hasMedia': bool(m.get('hasMedia') or m.get('mediaUrl')),
                                'mediaUrl': m.get('mediaUrl') or None,
                                'mentionedIds': m.get('mentionedIds') or [],
                                'mentions': m.get('mentions') or {},
                                'quoted': m.get('quoted') or None,
                                'draft': None,
                                'sent': False,
                            }
                            self._pending[mid] = entry
                            new_entries.append(entry)
                            msg_map[mid] = m  # Store bridge message for transcription
                        if new_entries or edit_entries:
                            _save_pending(self._pending)
                        # Advance only to data actually received. Advancing to "now"
                        # could skip a message arriving between the HTTP response and
                        # this assignment.
                        self._last_ts = newest_timestamp
                    # notify outside lock
                    for edit_entry in edit_entries:
                        for listener in list(self._edit_listeners):
                            try:
                                listener(edit_entry)
                            except Exception as e:
                                print(f"[WA] edit listener error: {e}", file=sys.stderr)
                    if self.on_new_message:
                        for entry in new_entries:
                            print(f"[WA] {entry.get('from')} → {entry.get('body','')[:60]}", file=sys.stderr)
                            try:
                                self.on_new_message(entry)
                            except Exception as e:
                                print(f"[WA] announce error: {e}", file=sys.stderr)
                    elif new_entries:
                        print(f"[WA] {len(new_entries)} msg(s), callback not set yet", file=sys.stderr)
                    # Shared queue: a YouTube/YT Music link in an incoming chat
                    # gets queued in the headless music player automatically.
                    for entry in new_entries:
                        if not entry.get('fromMe') and entry.get('body'):
                            video_id = _extract_youtube_id(entry['body'])
                            if video_id:
                                threading.Thread(
                                    target=self._queue_shared_track,
                                    args=(video_id,),
                                    daemon=True,
                                ).start()
                    # Auto-translate incoming foreign-language text (don't block polling)
                    for entry in new_entries:
                        if not entry.get('fromMe') and entry.get('body'):
                            threading.Thread(
                                target=self._translate_entry_text,
                                args=(entry,),
                                daemon=True,
                            ).start()
                    # Transcribe audio asynchronously (don't block polling)
                    for entry in new_entries:
                        msg_type = str(entry.get('type') or '').lower()
                        if msg_type in {'audio', 'ptt'}:
                            bridge_msg = msg_map.get(entry['id'])
                            if bridge_msg:
                                threading.Thread(
                                    target=self._transcribe_entry_audio,
                                    args=(entry, bridge_msg),
                                    daemon=True,
                                ).start()

                    # Notify any extra listeners (e.g. the live chat UI).
                    for entry in new_entries:
                        for listener in list(self._message_listeners):
                            try:
                                listener(entry)
                            except Exception as e:
                                print(f"[WA] listener error: {e}", file=sys.stderr)
                    for entry in new_entries:
                        self._schedule_auto_reply(entry)
            except Exception as e:
                _err_count += 1
                if _err_count == 1:
                    print(f"[WA] puente no disponible, reintentando en background...", file=sys.stderr)
                elif _err_count % 20 == 0:
                    # reminder every ~20 cycles
                    print(f"[WA] puente sigue sin responder ({_err_count} intentos).", file=sys.stderr)
                # exponential backoff: 1s, 2s, 4s, … capped at 60s
                _backoff = min(_backoff * 2, 60.0)
                time.sleep(_backoff)
                continue
            self._check_unanswered()
            time.sleep(self.poll_interval)

    def _check_unanswered(self) -> None:
        """Fire on_unanswered_reminder once per message left unanswered too long.

        Scanned at most once a minute (poll_interval runs every ~1.5s, this
        would otherwise re-check on every tick for no reason).
        """
        if not self.on_unanswered_reminder:
            return
        now = time.monotonic()
        if now - self._last_reminder_scan < 60:
            return
        self._last_reminder_scan = now
        now_ms = int(time.time() * 1000)
        threshold_ms = self.reminder_after_secs * 1000
        with self._lock:
            due = [
                entry for mid, entry in self._pending.items()
                if not entry.get('fromMe') and not entry.get('sent')
                and mid not in self._reminded
                and (now_ms - int(entry.get('timestamp') or 0)) >= threshold_ms
            ]
            for entry in due:
                self._reminded.add(entry['id'])
        for entry in due:
            try:
                self.on_unanswered_reminder(entry)
            except Exception as e:
                import sys
                print(f"[WA] reminder callback error: {e}", file=sys.stderr)

    def stop(self):
        self._stop.set()

    def _transcribe_entry_audio(self, entry: Dict[str, Any], msg_bridge: Dict[str, Any]) -> None:
        """Transcribe audio message asynchronously. Updates entry with 'transcription' key."""
        try:
            import sys
            transcript = transcribe_message_audio(msg_bridge)
            if transcript:
                entry['transcription'] = transcript
                with self._lock:
                    self._pending[entry['id']] = entry
                    _save_pending(self._pending)
                print(f"[WA] transcribed: {entry.get('from')} → {transcript[:100]}", file=sys.stderr)
            else:
                print(f"[WA] transcription failed for {entry.get('id')}", file=sys.stderr)
        except Exception as e:
            import sys
            print(f"[WA] transcription error: {e}", file=sys.stderr)

    def _queue_shared_track(self, video_id: str) -> None:
        try:
            from actions import ytmusic_headless as player
            player.add_to_queue(video_id)
        except Exception as e:
            import sys
            print(f"[WA] shared queue error: {e}", file=sys.stderr)

    def _translate_entry_text(self, entry: Dict[str, Any]) -> None:
        """Fill entry['translation'] if the incoming text isn't in Spanish.

        Mirrors _transcribe_entry_audio: best-effort, updates the stored
        pending entry in place so the next render of this message shows the
        inline translation (no live re-push to an already-open chat window,
        same tradeoff the audio transcription makes).
        """
        try:
            from actions.whatsapp_ai import translate_if_foreign
            translation = translate_if_foreign(entry.get('body', ''))
            if translation:
                entry['translation'] = translation
                with self._lock:
                    self._pending[entry['id']] = entry
                    _save_pending(self._pending)
        except Exception as e:
            import sys
            print(f"[WA] translation error: {e}", file=sys.stderr)

    def add_message_listener(self, listener) -> None:
        """Register a callable invoked with each new incoming message entry."""
        if listener is not None and listener not in self._message_listeners:
            self._message_listeners.append(listener)

    def remove_message_listener(self, listener) -> None:
        try:
            self._message_listeners.remove(listener)
        except ValueError:
            pass

    def add_edit_listener(self, listener) -> None:
        """Register a callable invoked with {'id','from','body','edited'} when a message is edited."""
        if listener is not None and listener not in self._edit_listeners:
            self._edit_listeners.append(listener)

    def remove_edit_listener(self, listener) -> None:
        try:
            self._edit_listeners.remove(listener)
        except ValueError:
            pass

    def start_auto_reply(self, contact: str, minutes: float) -> Dict[str, Any]:
        minutes = float(minutes)
        if minutes <= 0 or minutes > 24 * 60:
            raise ValueError("La duración debe estar entre 1 minuto y 24 horas.")
        chat_id = resolve_contact(contact, strict=True)
        if not chat_id:
            raise ValueError(f"No se encontró el contacto '{contact}'.")
        if str(chat_id).endswith("@g.us"):
            raise ValueError("La respuesta automática solo se habilita para contactos individuales.")
        session = {
            "chat_id": chat_id,
            "contact": str(contact),
            "started_at": time.time(),
            "expires_at": time.time() + minutes * 60,
            "minutes": minutes,
        }
        with self._lock:
            self._auto_reply_sessions[chat_id] = session
        return dict(session)

    def stop_auto_reply(self, contact: str = "") -> int:
        contact = str(contact or "").strip()
        with self._lock:
            if not contact:
                count = len(self._auto_reply_sessions)
                self._auto_reply_sessions.clear()
                return count
        chat_id = contact if "@" in contact else resolve_contact(contact, strict=True)
        with self._lock:
            return 1 if self._auto_reply_sessions.pop(chat_id, None) else 0

    def list_auto_replies(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            expired = [
                chat_id
                for chat_id, session in self._auto_reply_sessions.items()
                if float(session.get("expires_at") or 0) <= now
            ]
            for chat_id in expired:
                self._auto_reply_sessions.pop(chat_id, None)
            return [dict(session) for session in self._auto_reply_sessions.values()]

    def _schedule_auto_reply(self, entry: Dict[str, Any]):
        # Never auto-reply to our own outgoing messages (the poller also feeds
        # messages sent from the phone into the manager).
        if entry.get("fromMe"):
            return
        chat_id = str(entry.get("from") or "").strip()
        if not chat_id:
            return
        now = time.time()
        rule = None
        with self._lock:
            session = self._auto_reply_sessions.get(chat_id)
            if session and float(session.get("expires_at") or 0) <= now:
                self._auto_reply_sessions.pop(chat_id, None)
                session = None
            # Temporary voice/command sessions take precedence over rules.
            if not session:
                rule = self._match_rule(chat_id)
                if not rule:
                    return
            queued = dict(entry)
            queued["rule"] = rule  # None for temporary sessions
            self._auto_reply_queues.setdefault(chat_id, []).append(queued)
            if chat_id in self._auto_reply_busy:
                return
            self._auto_reply_busy.add(chat_id)
        threading.Thread(
            target=self._run_auto_reply_queue,
            args=(chat_id,),
            daemon=True,
        ).start()

    @staticmethod
    def _match_rule(chat_id: str) -> Optional[Dict[str, Any]]:
        """First persistent rule covering chat_id right now, or None."""
        try:
            from .whatsapp_rules import match_rule

            return match_rule(chat_id)
        except Exception:
            return None

    def _run_auto_reply_queue(self, chat_id: str):
        while not self._stop.is_set():
            with self._lock:
                queue = self._auto_reply_queues.get(chat_id) or []
                if not queue:
                    self._auto_reply_queues.pop(chat_id, None)
                    self._auto_reply_busy.discard(chat_id)
                    return
                entry = queue.pop(0)
            rule = entry.get("rule") or None
            # Re-validate per entry: a temporary session may have expired, or a
            # rule may have been disabled / fallen out of its schedule while the
            # message sat in the queue.
            if rule is None:
                with self._lock:
                    session = self._auto_reply_sessions.get(chat_id)
                    if not session or float(session.get("expires_at") or 0) <= time.time():
                        self._auto_reply_sessions.pop(chat_id, None)
                        continue
            elif self._match_rule(chat_id) is None:
                continue
            response_text = ""
            error = ""
            try:
                body = str(entry.get("body") or "")
                if rule:
                    from .whatsapp_ai import generate_rule_reply

                    response_text = str(
                        generate_rule_reply(chat_id, body, str(rule.get("prompt") or ""))
                    ).strip()
                else:
                    generator = self._reply_generator
                    if generator is None:
                        from .whatsapp_ai import generate_whatsapp_reply

                        generator = generate_whatsapp_reply
                    response_text = str(generator(chat_id, body)).strip()
                if not response_text:
                    raise RuntimeError("La IA generó una respuesta vacía.")
                self._message_sender(to=chat_id, body=response_text)
            except Exception as exc:
                error = str(exc)
            callback = self.on_auto_reply
            if callback:
                try:
                    callback(
                        {
                            "chat_id": chat_id,
                            "incoming": entry,
                            "response": response_text,
                            "error": error,
                        }
                    )
                except Exception:
                    pass

    def list_pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [v for v in self._pending.values() if not v.get('sent')]

    def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._pending.get(message_id)

    def mark_chat_read(self, chat_id: str) -> int:
        """Remove local pending entries belonging to a remotely read chat."""
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return 0
        removed = 0
        with self._lock:
            for message_id, entry in list(self._pending.items()):
                source = str(entry.get("from") or "").strip()
                if source == chat_id:
                    self._pending.pop(message_id, None)
                    self._reminded.discard(message_id)
                    removed += 1
            if removed:
                _save_pending(self._pending)
        return removed

    def prepare_reply(self, message_id: str, text: str):
        with self._lock:
            if message_id not in self._pending:
                raise KeyError('message_id not found')
            self._pending[message_id]['draft'] = text
            _save_pending(self._pending)

    def send_reply(self, message_id: str) -> Dict[str, Any]:
        with self._lock:
            if message_id not in self._pending:
                raise KeyError('message_id not found')
            entry = self._pending[message_id]
            draft = entry.get('draft')
            if not draft:
                raise ValueError('No draft prepared for this message')
            to = entry.get('from')
        # send outside lock
        resp = send_whatsapp(to=to, body=draft)
        with self._lock:
            entry['sent'] = True
            entry['sent_resp'] = resp
            _save_pending(self._pending)
        return resp


if __name__ == '__main__':
    mgr = WhatsAppManager()
    try:
        print('WhatsAppManager running. Polling messages...')
        while True:
            time.sleep(5)
            pend = mgr.list_pending()
            if pend:
                print('Pending messages:')
                for p in pend:
                    print(p['id'], p['from'], p['body'][:80])
    except KeyboardInterrupt:
        mgr.stop()
