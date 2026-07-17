from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .whatsapp import get_conversation


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _api_key() -> str:
    from actions.paths import config_path
    config = json.loads(
        config_path("api_keys.json").read_text(encoding="utf-8")
    )
    key = str(config.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("No hay una clave de Gemini configurada.")
    return key


def _message_line(message: dict[str, Any]) -> str:
    direction = str(message.get("direction") or "").lower()
    from_me = bool(message.get("fromMe")) or direction == "out"
    speaker = "Yo" if from_me else "Contacto"
    body = " ".join(str(message.get("body") or "").split())
    return f"{speaker}: {body}" if body else ""


def translate_if_foreign(body: str, target_lang: str = "español") -> str:
    """Translate incoming text if it isn't already in target_lang; else "".

    Used to show an inline translation subtitle under foreign-language
    messages. Cheap model, single short call — no conversation context needed.
    """
    from google import genai

    text = " ".join(str(body or "").split())
    if not text:
        return ""
    prompt = (
        f"Si el siguiente texto de WhatsApp NO está ya en {target_lang}, tradúcelo "
        f"al {target_lang} manteniendo el tono. Si ya está en {target_lang}, responde "
        "exactamente con una cadena vacía. Devuelve únicamente la traducción o la "
        f"cadena vacía, sin comillas ni explicación.\n\nTexto: {text}"
    )
    client = genai.Client(api_key=_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
    )
    return str(getattr(response, "text", "") or "").strip().strip('"')


def generate_whatsapp_reply(
    chat_id: str,
    incoming_body: str = "",
    messages: list[dict[str, Any]] | None = None,
) -> str:
    """Generate a draft matching the user's tone without sending it."""
    from google import genai

    context = list(messages or [])
    if not context:
        context = get_conversation(chat_id, limit=24, timeout=20, strict=True)
    transcript = "\n".join(
        line for line in (_message_line(message) for message in context[-24:]) if line
    )
    incoming_body = " ".join(str(incoming_body or "").split())
    if incoming_body and not transcript.endswith(f"Contacto: {incoming_body}"):
        transcript = f"{transcript}\nContacto: {incoming_body}".strip()

    prompt = (
        "Redacta la siguiente respuesta de WhatsApp como si fueras el usuario. "
        "Imita el idioma, tono, longitud y nivel de confianza de sus mensajes anteriores. "
        "Responde al último mensaje recibido. No inventes datos ni compromisos. "
        "Devuelve únicamente el texto que se enviaría, sin comillas, etiquetas ni explicación.\n\n"
        f"Conversación:\n{transcript}"
    )
    client = genai.Client(api_key=_api_key())
    response = client.models.generate_content(
        model="gemma-4-26b-a4b-it",
        contents=prompt,
    )
    text = str(getattr(response, "text", "") or "").strip().strip('"')
    if not text:
        raise RuntimeError("La IA no generó ninguna respuesta.")
    return text


# ── Rule-driven reply (custom prompt + read-only calendar access) ────────────
_CALENDAR_TOOL = {
    "name": "google_calendar",
    "description": (
        "Consulta el Google Calendar del usuario para saber su disponibilidad o "
        "sus próximos eventos antes de responder. Solo lectura."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list_events | search_events"},
            "query": {"type": "STRING", "description": "Texto a buscar (search_events)"},
            "limit": {"type": "INTEGER", "description": "Máximo de eventos (por defecto 10)"},
        },
        "required": ["action"],
    },
}


def _run_calendar(args: dict) -> str:
    """Execute a read-only calendar lookup for the reply model. Never raises."""
    try:
        from actions.google_calendar import google_calendar

        action = str(args.get("action") or "list_events")
        if action not in {"list_events", "search_events"}:
            action = "list_events"
        return str(google_calendar(parameters={**args, "action": action}))
    except Exception as exc:  # calendar not configured / offline → degrade
        return f"No se pudo consultar el calendario: {exc}"


def generate_rule_reply(
    chat_id: str,
    incoming_body: str,
    custom_prompt: str,
    messages: list[dict[str, Any]] | None = None,
    max_calendar_calls: int = 3,
) -> str:
    """Draft an auto-reply driven by a rule's custom prompt.

    The model receives the recent conversation and the rule's instructions, and
    may call ``google_calendar`` (read-only) to check the user's availability
    before answering. Calendar access degrades gracefully if not configured.
    """
    from google import genai
    from google.genai import types

    context = list(messages or [])
    if not context:
        context = get_conversation(chat_id, limit=24, timeout=20, strict=True)
    transcript = "\n".join(
        line for line in (_message_line(message) for message in context[-24:]) if line
    )
    incoming_body = " ".join(str(incoming_body or "").split())
    if incoming_body and not transcript.endswith(f"Contacto: {incoming_body}"):
        transcript = f"{transcript}\nContacto: {incoming_body}".strip()

    system_instruction = (
        "Eres el usuario respondiendo a WhatsApp en su lugar siguiendo estas "
        "instrucciones. Imita su idioma y tono. No inventes datos ni adquieras "
        "compromisos que no estén respaldados por las instrucciones o el "
        "calendario. Si necesitas saber la disponibilidad o agenda del usuario, "
        "usa la herramienta google_calendar (solo lectura) antes de responder. "
        "Devuelve únicamente el texto que se enviaría, sin comillas ni "
        "explicaciones.\n\n"
        f"Instrucciones de la regla:\n{custom_prompt.strip()}"
    )

    client = genai.Client(api_key=_api_key())
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[types.Tool(function_declarations=[_CALENDAR_TOOL])],
    )
    contents: list[Any] = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"Conversación:\n{transcript}")],
        )
    ]

    for _ in range(max_calendar_calls + 1):
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=contents,
            config=config,
        )
        candidate = (response.candidates or [None])[0]
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        if not calls:
            break
        # Echo the model turn, then answer each calendar call before looping.
        contents.append(candidate.content)
        tool_parts = []
        for call in calls:
            output = _run_calendar(dict(call.args or {}))
            tool_parts.append(
                types.Part.from_function_response(
                    name=call.name, response={"result": output}
                )
            )
        contents.append(types.Content(role="tool", parts=tool_parts))

    text = str(getattr(response, "text", "") or "").strip().strip('"')
    if not text:
        raise RuntimeError("La IA no generó ninguna respuesta.")
    return text
