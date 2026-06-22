from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


def _dt_value(event: dict) -> str:
    start = event.get("start", {})
    if isinstance(start, dict):
        return start.get("dateTime") or start.get("date") or ""
    return str(start or "")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "si", "sí"}


def whatsapp_recent(limit: int = 10, days: int = 2) -> list[dict]:
    from actions import whatsapp

    if not whatsapp.ensure_bridge_ready():
        return [{"error": "WhatsApp bridge no esta conectado."}]
    since_ms = int((time.time() - (max(1, int(days or 2)) * 86400)) * 1000)
    messages = whatsapp.fetch_messages(since_ms=since_ms)
    messages.sort(key=lambda m: int(m.get("timestamp", 0) or 0), reverse=True)
    return [
        {
            "id": m.get("id"),
            "from": m.get("from"),
            "to": m.get("to"),
            "body": m.get("body", ""),
            "timestamp": m.get("timestamp"),
        }
        for m in messages[:max(1, int(limit or 10))]
    ]


def whatsapp_search(query: str, contact: str = "", limit: int = 20, days: int = 14) -> list[dict]:
    from actions import whatsapp

    if not whatsapp.ensure_bridge_ready():
        return [{"error": "WhatsApp bridge no esta conectado."}]
    needle = str(query or "").lower().strip()
    if not needle:
        return []

    if contact:
        messages = whatsapp.get_conversation(contact, limit=max(50, int(limit or 20) * 4))
    else:
        since_ms = int((time.time() - (max(1, int(days or 14)) * 86400)) * 1000)
        messages = whatsapp.fetch_messages(since_ms=since_ms)

    matches = []
    for m in messages:
        body = str(m.get("body") or "")
        if needle in body.lower():
            matches.append({
                "id": m.get("id"),
                "from": m.get("from"),
                "to": m.get("to"),
                "body": body,
                "timestamp": m.get("timestamp"),
            })
    matches.sort(key=lambda m: int(m.get("timestamp", 0) or 0), reverse=True)
    return matches[:max(1, int(limit or 20))]


def _calendar_service():
    from actions.google_calendar import _get_service

    return _get_service()


def calendar_today(calendar_id: str = "primary") -> list[dict]:
    svc = _calendar_service()
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    resp = svc.events().list(
        calendarId=calendar_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return [
        {
            "id": e.get("id"),
            "summary": e.get("summary", "(sin titulo)"),
            "start": _dt_value(e),
            "location": e.get("location", ""),
            "link": e.get("htmlLink", ""),
        }
        for e in resp.get("items", [])
    ]


def calendar_next(limit: int = 5, calendar_id: str = "primary") -> list[dict]:
    from actions.google_calendar import list_events

    return list_events(max_results=max(1, int(limit or 5)), calendar_id=calendar_id)


def calendar_freebusy(hours: int = 8, calendar_id: str = "primary") -> dict:
    svc = _calendar_service()
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=max(1, int(hours or 8)))
    body = {
        "timeMin": now.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": calendar_id}],
    }
    resp = svc.freebusy().query(body=body).execute()
    busy = resp.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return {
        "from": now.isoformat(),
        "to": end.isoformat(),
        "busy": busy,
        "busy_count": len(busy),
        "free_now": len(busy) == 0 or not any(b.get("start") <= now.isoformat() <= b.get("end") for b in busy),
    }


def email_summary(count: int = 10, unread_only: bool = True, label: str = "INBOX") -> dict:
    from actions.gmail import list_emails

    emails = list_emails(count=max(1, int(count or 10)), label=label or "INBOX", unread_only=bool(unread_only))
    return {
        "label": label or "INBOX",
        "unread_only": bool(unread_only),
        "count": len(emails),
        "emails": [
            {
                "id": e.get("id"),
                "from": e.get("from", ""),
                "subject": e.get("subject", ""),
                "date": e.get("date", ""),
                "snippet": e.get("snippet", ""),
                "unread": e.get("unread", False),
            }
            for e in emails
        ],
    }


def productivity_tools(parameters: dict, player=None, speak=None):
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()
    if player:
        player.write_log(f"[Productivity] {action}")

    if action == "whatsapp_recent":
        return whatsapp_recent(limit=int(params.get("limit") or 10), days=int(params.get("days") or 2))
    if action == "whatsapp_search":
        return whatsapp_search(
            query=params.get("query", ""),
            contact=params.get("contact") or params.get("to") or "",
            limit=int(params.get("limit") or 20),
            days=int(params.get("days") or 14),
        )
    if action == "calendar_today":
        return calendar_today(calendar_id=params.get("calendar_id", "primary"))
    if action == "calendar_next":
        return calendar_next(limit=int(params.get("limit") or 5), calendar_id=params.get("calendar_id", "primary"))
    if action == "calendar_freebusy":
        return calendar_freebusy(hours=int(params.get("hours") or 8), calendar_id=params.get("calendar_id", "primary"))
    if action == "email_summary":
        return email_summary(
            count=int(params.get("count") or params.get("limit") or 10),
            unread_only=_as_bool(params.get("unread_only", True)),
            label=params.get("label", "INBOX"),
        )

    return (
        "Accion desconocida. Usa whatsapp_recent, whatsapp_search, calendar_today, "
        "calendar_next, calendar_freebusy o email_summary."
    )
