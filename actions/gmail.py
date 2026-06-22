"""Gmail integration for Jarvis.

Acciones disponibles:
- list_emails   : lista correos recientes del inbox (o cualquier label)
- search_emails : busca correos con query estilo Gmail
- read_email    : lee el contenido completo de un correo por ID
- send_email    : envía un correo

Autenticación: compartida con Calendar y Drive (config/google_token.json).
"""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import subprocess
import tempfile
import threading
import time
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional


from actions.paths import memory_path
_CACHE_PATH = memory_path("gmail_cache.json")
_CACHE_LOCK = threading.RLock()
_INDEX_TTL = 300
_METADATA_TTL = 900
_BODY_TTL = 7 * 24 * 60 * 60
_BODY_CACHE_VERSION = 3
_cache_data: dict | None = None


def _get_service():
    from actions.google_auth import get_google_service
    return get_google_service("gmail", "v1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            value = h.get("value", "")
            try:
                return str(make_header(decode_header(value)))
            except Exception:
                return value
    return ""


def _load_cache() -> dict:
    global _cache_data
    with _CACHE_LOCK:
        if _cache_data is not None:
            return _cache_data
        try:
            loaded = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                loaded = {}
        except (OSError, ValueError):
            loaded = {}
        loaded.setdefault("indexes", {})
        loaded.setdefault("metadata", {})
        loaded.setdefault("bodies", {})
        _cache_data = loaded
        return _cache_data


def _save_cache() -> None:
    with _CACHE_LOCK:
        cache = _load_cache()
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = _CACHE_PATH.with_suffix(".tmp")
        temp.write_text(
            json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temp.replace(_CACHE_PATH)


def _cache_fresh(entry: dict | None, ttl: int) -> bool:
    if not isinstance(entry, dict):
        return False
    return (time.time() - float(entry.get("cached_at") or 0)) < ttl


def _query_key(query: str, label_ids: list[str] | None) -> str:
    return json.dumps(
        {"query": str(query or ""), "labels": label_ids or []},
        sort_keys=True,
        separators=(",", ":"),
    )


def _list_request_parts(
    label: str = "INBOX",
    unread_only: bool = False,
    query: str = "",
) -> tuple[str, list[str] | None]:
    q_parts = [str(query or "").strip()]
    if unread_only:
        q_parts.append("is:unread")
    label = str(label or "").strip()
    if label and label.upper() not in {"ALL", "ANYWHERE", "ALL_MAIL", "TODO"}:
        label_ids = [label]
    else:
        label_ids = None
        if not query:
            q_parts.append("in:anywhere")
    return " ".join(part for part in q_parts if part), label_ids


def _message_summary(message: dict) -> Dict:
    headers = message.get("payload", {}).get("headers", [])
    return {
        "id": message["id"],
        "from": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "snippet": message.get("snippet", ""),
        "unread": "UNREAD" in message.get("labelIds", []),
    }


def _all_message_ids(query: str, label_ids: list[str] | None, refresh: bool = False) -> list[str]:
    cache = _load_cache()
    key = _query_key(query, label_ids)
    cached = cache["indexes"].get(key)
    if not refresh and _cache_fresh(cached, _INDEX_TTL):
        return list(cached.get("ids") or [])

    svc = _get_service()
    ids: list[str] = []
    page_token = None
    while True:
        request = {
            "userId": "me",
            "q": query,
            "maxResults": 500,
        }
        if label_ids:
            request["labelIds"] = label_ids
        if page_token:
            request["pageToken"] = page_token
        response = svc.users().messages().list(**request).execute()
        ids.extend(item["id"] for item in response.get("messages", []) if item.get("id"))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    with _CACHE_LOCK:
        cache["indexes"][key] = {"cached_at": time.time(), "ids": ids}
        _save_cache()
    return ids


def _message_metadata(message_ids: list[str]) -> list[Dict]:
    cache = _load_cache()
    now = time.time()
    result_by_id: dict[str, Dict] = {}
    missing: list[str] = []

    with _CACHE_LOCK:
        for message_id in message_ids:
            entry = cache["metadata"].get(message_id)
            if _cache_fresh(entry, _METADATA_TTL):
                result_by_id[message_id] = dict(entry["data"])
            else:
                missing.append(message_id)

    if missing:
        svc = _get_service()
        fetched: dict[str, Dict] = {}
        errors: list[Exception] = []

        def callback(request_id, response, exception):
            if exception is not None:
                errors.append(exception)
            elif response:
                fetched[request_id] = _message_summary(response)

        batch = svc.new_batch_http_request(callback=callback)
        for message_id in missing:
            batch.add(
                svc.users().messages().get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ),
                request_id=message_id,
            )
        batch.execute()

        if errors and not fetched:
            raise errors[0]

        with _CACHE_LOCK:
            for message_id, data in fetched.items():
                cache["metadata"][message_id] = {"cached_at": now, "data": data}
                result_by_id[message_id] = data
            _save_cache()

    return [result_by_id[mid] for mid in message_ids if mid in result_by_id]


def _part_charset(part: dict) -> str:
    for header in part.get("headers", []) or []:
        if str(header.get("name", "")).lower() != "content-type":
            continue
        match = re.search(
            r"charset\s*=\s*[\"']?([^;\"'\s]+)",
            str(header.get("value", "")),
            flags=re.I,
        )
        if match:
            return match.group(1).strip()
    return "utf-8"


def _b64url_decode(data: str, charset: str = "utf-8") -> str:
    if not data:
        return ""
    pad = "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(data + pad)
    encodings = [charset, "utf-8", "windows-1252", "latin-1"]
    tried: set[str] = set()
    for encoding in encodings:
        normalized = str(encoding or "").strip().lower()
        if not normalized or normalized in tried:
            continue
        tried.add(normalized)
        try:
            return raw.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _b64url_bytes(data: str) -> bytes:
    if not data:
        return b""
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _iter_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _iter_parts(part)


def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return _b64url_decode(data, _part_charset(payload))

    # Recurse into parts first (prefer plain over html)
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    # Fallback: strip HTML
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            raw_html = _b64url_decode(data, _part_charset(payload))
            return re.sub(r"<[^>]+>", " ", raw_html).strip()

    return ""


def _extract_html(payload: dict) -> str:
    """Prefer HTML body when available, otherwise return an empty string."""
    for part in _iter_parts(payload):
        mime = str(part.get("mimeType", "")).lower()
        body = part.get("body", {}) or {}
        data = body.get("data", "")
        if mime == "text/html" and data:
            raw_html = _b64url_decode(data, _part_charset(part))
            raw_html = re.sub(r"<script\b[^>]*>.*?</script>", "", raw_html, flags=re.I | re.S)
            return raw_html
    return ""


def _extract_inline_images(payload: dict, email_id: str) -> List[Dict]:
    images: List[Dict] = []
    for part in _iter_parts(payload):
        mime = str(part.get("mimeType", "")).lower()
        if not mime.startswith("image/"):
            continue
        body = part.get("body", {}) or {}
        data = body.get("data", "")
        attachment_id = body.get("attachmentId", "")
        headers = part.get("headers", []) or []
        cid = ""
        for h in headers:
            if str(h.get("name", "")).lower() == "content-id":
                cid = str(h.get("value", "")).strip("<>")
                break
        if not cid:
            cid = str(part.get("filename", "") or attachment_id or "")
        if not cid:
            continue
        raw = _b64url_bytes(data) if data else b""
        if not raw and attachment_id:
            try:
                svc = _get_service()
                att = svc.users().messages().attachments().get(
                    userId="me",
                    messageId=email_id,
                    id=attachment_id,
                ).execute()
                raw = _b64url_bytes(att.get("data", ""))
            except Exception:
                raw = b""
        if not raw:
            continue
        content_type = mime or mimetypes.guess_type(part.get("filename", ""))[0] or "image/png"
        images.append({
            "cid": cid,
            "content_type": content_type,
            "data_url": f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}",
        })
    return images


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def list_emails(
    count: int = 10,
    label: str = "INBOX",
    unread_only: bool = False,
) -> List[Dict]:
    page = get_email_page(page=1, page_size=max(1, int(count)), label=label, unread_only=unread_only)
    return page["emails"]


def search_emails(query: str, count: int = 10) -> List[Dict]:
    page = get_email_page(page=1, page_size=max(1, int(count)), label="ALL", query=query)
    return page["emails"]


def recent_emails(count: int = 100) -> List[Dict]:
    return search_emails("in:anywhere", count=count)


def get_email_page(
    page: int = 1,
    page_size: int = 50,
    label: str = "INBOX",
    unread_only: bool = False,
    query: str = "",
    refresh: bool = False,
) -> Dict:
    page_size = max(1, min(100, int(page_size or 50)))
    query_text, label_ids = _list_request_parts(label, unread_only, query)
    message_ids = _all_message_ids(query_text, label_ids, refresh=refresh)
    total = len(message_ids)
    pages = max(1, math.ceil(total / page_size))
    page = max(1, min(int(page or 1), pages))
    start = (page - 1) * page_size
    page_ids = message_ids[start:start + page_size]
    return {
        "emails": _message_metadata(page_ids),
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": page_size,
    }


def read_email(email_id: str) -> Dict:
    cache = _load_cache()
    with _CACHE_LOCK:
        cached = cache["bodies"].get(email_id)
        if (
            _cache_fresh(cached, _BODY_TTL)
            and int(cached.get("version") or 0) == _BODY_CACHE_VERSION
        ):
            return dict(cached["data"])

    svc  = _get_service()
    m    = svc.users().messages().get(userId="me", id=email_id, format="full").execute()
    hdrs = m.get("payload", {}).get("headers", [])
    payload = m.get("payload", {})
    body = _extract_body(payload)
    html_body = _extract_html(payload)
    result = {
        "id":      m["id"],
        "from":    _header(hdrs, "From"),
        "to":      _header(hdrs, "To"),
        "subject": _header(hdrs, "Subject"),
        "date":    _header(hdrs, "Date"),
        "html":    html_body,
        "body":    body,
    }
    with _CACHE_LOCK:
        cache["bodies"][email_id] = {
            "cached_at": time.time(),
            "version": _BODY_CACHE_VERSION,
            "data": result,
        }
        _save_cache()
    return result


def read_email_images(email_id: str) -> Dict:
    svc = _get_service()
    message = svc.users().messages().get(userId="me", id=email_id, format="full").execute()
    payload = message.get("payload", {})
    return {
        "id": email_id,
        "inline_images": _extract_inline_images(payload, email_id),
    }


def render_email_preview(email_id: str, html_body: str, width: int = 760) -> Dict:
    """Render a complex newsletter with Edge and cache the resulting image."""
    digest = __import__("hashlib").sha256(html_body.encode("utf-8")).hexdigest()[:16]
    render_dir = memory_path("gmail_render")
    render_dir.mkdir(parents=True, exist_ok=True)
    image_path = render_dir / f"{email_id}-{digest}-{int(width)}.png"
    if image_path.exists() and image_path.stat().st_size > 0:
        return {"id": email_id, "image_path": str(image_path)}

    width = max(640, min(1200, int(width or 760)))
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.exists():
        edge = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    if not edge.exists():
        raise FileNotFoundError("Microsoft Edge no está instalado.")

    document = re.sub(r"<script\b[^>]*>.*?</script>", "", html_body, flags=re.I | re.S)
    viewport_height = 16000
    with tempfile.TemporaryDirectory(prefix="jarvis-mail-") as temp_dir:
        temp_root = Path(temp_dir)
        html_path = temp_root / "mail.html"
        raw_screenshot = temp_root / "mail.png"
        profile_dir = temp_root / "edge-profile"
        html_path.write_text(document, encoding="utf-8")
        command = [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-networking",
            "--virtual-time-budget=5000",
            f"--user-data-dir={profile_dir}",
            f"--window-size={width},{viewport_height}",
            f"--screenshot={raw_screenshot}",
            html_path.as_uri(),
        ]
        subprocess.run(
            command,
            check=False,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.time() + 5
        while time.time() < deadline and not raw_screenshot.exists():
            time.sleep(0.1)
        if not raw_screenshot.exists():
            raise RuntimeError("Edge no pudo renderizar el correo.")

        from PIL import Image, ImageChops

        with Image.open(raw_screenshot).convert("RGB") as image:
            background = Image.new("RGB", image.size, image.getpixel((image.width - 1, image.height - 1)))
            difference = ImageChops.difference(image, background)
            bbox = difference.getbbox()
            bottom = min(image.height, (bbox[3] if bbox else 900) + 40)
            image.crop((0, 0, image.width, max(900, bottom))).save(image_path, "PNG")
    return {"id": email_id, "image_path": str(image_path)}


def send_email(to: str, subject: str, body: str) -> str:
    svc = _get_service()
    msg = MIMEText(body)
    msg["to"]      = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Correo enviado a {to}."


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def gmail(parameters: dict, player=None, speak=None) -> str:
    """Main entry point called by Jarvis _execute_tool."""
    action = parameters.get("action", "list_emails")

    try:
        if action == "list_emails":
            count      = int(parameters.get("count") or 10)
            label      = parameters.get("label") or "INBOX"
            unread_only = bool(parameters.get("unread_only", False))
            emails     = list_emails(count, label, unread_only)
            if not emails:
                return "No hay correos."
            lines = [f"{len(emails)} correo(s) en {label}:"]
            for e in emails:
                mark = " [no leído]" if e.get("unread") else ""
                lines.append(f"- De: {e['from']} | {e['subject']}{mark}\n  {e['snippet'][:100]}")
            return "\n".join(lines)

        elif action == "search_emails":
            query = parameters.get("query") or ""
            count = int(parameters.get("count") or 10)
            if not query:
                return "Necesito una búsqueda."
            emails = search_emails(query, count)
            if not emails:
                return f"No se encontraron correos para '{query}'."
            lines = [f"Resultados para '{query}':"]
            for e in emails:
                lines.append(f"- {e['from']} | {e['subject']} | {e['snippet'][:100]}")
            return "\n".join(lines)

        elif action == "read_email":
            email_id = parameters.get("email_id") or parameters.get("id")
            if not email_id:
                return "Necesito el ID del correo."
            e = read_email(email_id)
            return f"De: {e['from']}\nAsunto: {e['subject']}\nFecha: {e['date']}\n\n{e['body']}"

        elif action == "send_email":
            to      = parameters.get("to") or ""
            subject = parameters.get("subject") or "(sin asunto)"
            body    = parameters.get("body") or ""
            if not to:
                return "Necesito el destinatario."
            return send_email(to, subject, body)

        else:
            return f"Acción desconocida: {action}. Usa list_emails, search_emails, read_email o send_email."

    except FileNotFoundError as e:
        return str(e)
    except Exception as e:
        err = str(e)
        if any(k in err.lower() for k in ("invalid_grant", "token", "unauthorized", "403", "401")):
            try:
                from actions.auth_dialog import show_gcal_setup_dialog
                show_gcal_setup_dialog()
            except Exception:
                pass
            return f"Gmail: error de autenticación — {err}"
        return f"Gmail error: {err}"
