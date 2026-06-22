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
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = str(getattr(response, "text", "") or "").strip().strip('"')
    if not text:
        raise RuntimeError("La IA no generó ninguna respuesta.")
    return text
