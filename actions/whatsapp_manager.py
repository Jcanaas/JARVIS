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
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from .whatsapp import fetch_messages, is_ignored_message, resolve_contact, send_whatsapp

from actions.paths import RESOURCE_DIR, DATA_DIR
ROOT = RESOURCE_DIR
PENDING_FILE = DATA_DIR / "whatsapp_pending.json"


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
    ):
        self.poll_interval = poll_interval
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
                    newest_timestamp = self._last_ts
                    with self._lock:
                        for m in msgs:
                            if is_ignored_message(m):
                                continue
                            # skip outgoing messages
                            if m.get('direction') == 'out':
                                continue
                            mid = m.get('id') or f"{m.get('from')}_{m.get('timestamp')}"
                            try:
                                newest_timestamp = max(newest_timestamp, int(m.get("timestamp") or 0))
                            except (TypeError, ValueError):
                                pass
                            # deduplicate using session-only _seen set
                            if mid in self._seen:
                                continue
                            self._seen.add(mid)
                            entry = {
                                'id': mid,
                                'from': m.get('from'),
                                'author': m.get('author') or None,
                                'senderName': m.get('senderName') or None,
                                'body': m.get('body') or '',
                                'type': m.get('type', 'chat'),
                                'timestamp': m.get('timestamp'),
                                'draft': None,
                                'sent': False,
                            }
                            self._pending[mid] = entry
                            new_entries.append(entry)
                        if new_entries:
                            _save_pending(self._pending)
                        # Advance only to data actually received. Advancing to "now"
                        # could skip a message arriving between the HTTP response and
                        # this assignment.
                        self._last_ts = newest_timestamp
                    # notify outside lock
                    if self.on_new_message:
                        for entry in new_entries:
                            print(f"[WA] {entry.get('from')} → {entry.get('body','')[:60]}", file=sys.stderr)
                            try:
                                self.on_new_message(entry)
                            except Exception as e:
                                print(f"[WA] announce error: {e}", file=sys.stderr)
                    elif new_entries:
                        print(f"[WA] {len(new_entries)} msg(s), callback not set yet", file=sys.stderr)
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
            time.sleep(self.poll_interval)

    def stop(self):
        self._stop.set()

    def add_message_listener(self, listener) -> None:
        """Register a callable invoked with each new incoming message entry."""
        if listener is not None and listener not in self._message_listeners:
            self._message_listeners.append(listener)

    def remove_message_listener(self, listener) -> None:
        try:
            self._message_listeners.remove(listener)
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
        chat_id = str(entry.get("from") or "").strip()
        if not chat_id:
            return
        now = time.time()
        with self._lock:
            session = self._auto_reply_sessions.get(chat_id)
            if not session:
                return
            if float(session.get("expires_at") or 0) <= now:
                self._auto_reply_sessions.pop(chat_id, None)
                return
            self._auto_reply_queues.setdefault(chat_id, []).append(dict(entry))
            if chat_id in self._auto_reply_busy:
                return
            self._auto_reply_busy.add(chat_id)
        threading.Thread(
            target=self._run_auto_reply_queue,
            args=(chat_id,),
            daemon=True,
        ).start()

    def _run_auto_reply_queue(self, chat_id: str):
        while not self._stop.is_set():
            with self._lock:
                queue = self._auto_reply_queues.get(chat_id) or []
                session = self._auto_reply_sessions.get(chat_id)
                if not queue or not session or float(session.get("expires_at") or 0) <= time.time():
                    self._auto_reply_queues.pop(chat_id, None)
                    self._auto_reply_busy.discard(chat_id)
                    return
                entry = queue.pop(0)
            response_text = ""
            error = ""
            try:
                generator = self._reply_generator
                if generator is None:
                    from .whatsapp_ai import generate_whatsapp_reply

                    generator = generate_whatsapp_reply
                response_text = str(
                    generator(chat_id, str(entry.get("body") or ""))
                ).strip()
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
