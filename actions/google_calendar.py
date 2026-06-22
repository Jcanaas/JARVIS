"""Google Calendar integration for Jarvis.

Acciones disponibles:
- list_events   : lista eventos próximos
- create_event  : crea un evento
- delete_event  : elimina un evento por ID
- search_events : busca eventos por texto

Configuración inicial:
1. Ve a https://console.cloud.google.com/
2. Crea un proyecto → Habilita "Google Calendar API"
3. Credenciales → OAuth 2.0 → Tipo: Desktop App
4. Descarga el JSON y guárdalo como config/google_credentials.json
5. La primera vez que uses la herramienta se abrirá el navegador para autorizar.
   El token se guardará en config/google_token.json y no volverá a pedirse.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from actions.paths import config_path

CREDENTIALS_FILE = config_path("google_credentials.json")
TOKEN_FILE       = config_path("google_token.json")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_service():
    from actions.google_auth import get_google_service
    return get_google_service("calendar", "v3")


def _parse_dt(dt_str: str) -> str:
    """Intenta parsear una cadena de fecha/hora libre y devolverla en RFC3339."""
    from dateutil import parser as dtparser
    dt = dtparser.parse(dt_str, dayfirst=True)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.isoformat()


def list_events(max_results: int = 10, calendar_id: str = "primary") -> List[Dict]:
    svc   = _get_service()
    now   = datetime.now(timezone.utc).isoformat()
    resp  = svc.events().list(
        calendarId=calendar_id,
        timeMin=now,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    items = resp.get("items", [])
    out   = []
    for e in items:
        start = e.get("start", {})
        out.append({
            "id":       e.get("id"),
            "summary":  e.get("summary", "(sin título)"),
            "start":    start.get("dateTime") or start.get("date"),
            "location": e.get("location", ""),
            "link":     e.get("htmlLink", ""),
        })
    return out


def create_event(
    summary: str,
    start: str,
    end: Optional[str] = None,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
) -> Dict:
    svc        = _get_service()
    start_rfc  = _parse_dt(start)
    if end:
        end_rfc = _parse_dt(end)
    else:
        # default: 1 hour after start
        from dateutil import parser as dtparser
        end_rfc = (dtparser.parse(start_rfc) + timedelta(hours=1)).isoformat()

    body = {
        "summary":     summary,
        "description": description,
        "location":    location,
        "start":       {"dateTime": start_rfc},
        "end":         {"dateTime": end_rfc},
    }
    event = svc.events().insert(calendarId=calendar_id, body=body).execute()
    return {"id": event.get("id"), "summary": event.get("summary"), "link": event.get("htmlLink")}


def delete_event(event_id: str, calendar_id: str = "primary") -> str:
    svc = _get_service()
    svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return f"Event {event_id} deleted."


def search_events(query: str, max_results: int = 10, calendar_id: str = "primary") -> List[Dict]:
    svc  = _get_service()
    resp = svc.events().list(
        calendarId=calendar_id,
        q=query,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    items = resp.get("items", [])
    out   = []
    for e in items:
        start = e.get("start", {})
        out.append({
            "id":      e.get("id"),
            "summary": e.get("summary", "(sin título)"),
            "start":   start.get("dateTime") or start.get("date"),
            "link":    e.get("htmlLink", ""),
        })
    return out


def google_calendar(parameters: dict, player=None, speak=None) -> str:
    """Punto de entrada principal. Recibe args del tool y despacha."""
    action      = parameters.get("action", "list_events")
    calendar_id = parameters.get("calendar_id", "primary")

    try:
        if action == "list_events":
            limit  = int(parameters.get("limit") or 10)
            events = list_events(max_results=limit, calendar_id=calendar_id)
            if not events:
                return "No tienes eventos próximos."
            lines = [f"Próximos {len(events)} evento(s):"]
            for e in events:
                lines.append(f"- {e['summary']} | {e['start']}")
            return "\n".join(lines)

        elif action == "create_event":
            summary     = parameters.get("summary") or parameters.get("title") or ""
            start       = parameters.get("start") or parameters.get("start_time") or ""
            end         = parameters.get("end")   or parameters.get("end_time")
            description = parameters.get("description", "")
            location    = parameters.get("location", "")
            if not summary or not start:
                return "Necesito al menos el título y la fecha/hora de inicio."
            ev = create_event(summary, start, end, description, location, calendar_id)
            return f"Evento creado: '{ev['summary']}' — {ev.get('link', '')}"

        elif action == "delete_event":
            event_id = parameters.get("event_id") or parameters.get("id")
            if not event_id:
                return "Necesito el ID del evento para eliminarlo."
            return delete_event(event_id, calendar_id)

        elif action == "search_events":
            query  = parameters.get("query") or parameters.get("search") or ""
            limit  = int(parameters.get("limit") or 10)
            events = search_events(query, max_results=limit, calendar_id=calendar_id)
            if not events:
                return f"No se encontraron eventos para '{query}'."
            lines = [f"Resultados para '{query}':"]
            for e in events:
                lines.append(f"- {e['summary']} | {e['start']}")
            return "\n".join(lines)

        else:
            return f"Acción desconocida: {action}. Usa list_events, create_event, delete_event o search_events."

    except FileNotFoundError as e:
        return str(e)
    except Exception as e:
        err_str = str(e)
        # Catch auth/token errors and show setup dialog
        if any(k in err_str.lower() for k in ("invalid_grant", "token", "credentials", "unauthorized", "403", "401")):
            try:
                from actions.auth_dialog import show_gcal_setup_dialog
                show_gcal_setup_dialog()
            except Exception:
                pass
            return f"Google Calendar: error de autenticación — {err_str}"
        return f"Google Calendar error: {err_str}"
