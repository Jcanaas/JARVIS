"""Asistencia de IA para redactar correos (Gemini), sin enviarlos."""
from __future__ import annotations

import json
import re
from typing import Dict


def _api_key() -> str:
    from actions.paths import config_path

    config = json.loads(config_path("api_keys.json").read_text(encoding="utf-8"))
    key = str(config.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("No hay una clave de Gemini configurada.")
    return key


def _strip_json_fence(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def draft_email(instruction: str, to: str = "", subject: str = "") -> Dict[str, str]:
    """Redacta asunto + cuerpo de un correo a partir de una instrucción.

    Devuelve {"subject": ..., "body": ...}. No envía nada.
    """
    from google import genai

    instruction = " ".join(str(instruction or "").split())
    if not instruction:
        raise RuntimeError("Escribe una instrucción para la IA.")

    recipient = str(to or "").strip()
    existing_subject = str(subject or "").strip()

    prompt = (
        "Eres un asistente que redacta correos electrónicos en nombre del usuario. "
        "A partir de la instrucción, escribe un correo claro, educado y natural. "
        "Usa el mismo idioma de la instrucción. No inventes datos concretos "
        "(fechas, cifras, nombres) que no aparezcan en la instrucción; si faltan, "
        "deja la redacción genérica. Firma de forma neutra si procede.\n"
        "Devuelve ÚNICAMENTE un objeto JSON con las claves \"subject\" y \"body\", "
        "sin texto adicional ni comillas de código.\n\n"
        f"Destinatario: {recipient or '(no especificado)'}\n"
        f"Asunto actual: {existing_subject or '(vacío, propón uno)'}\n"
        f"Instrucción: {instruction}"
    )

    client = genai.Client(api_key=_api_key())
    response = client.models.generate_content(
        model="gemma-4-26b-a4b-it",
        contents=prompt,
    )
    raw = _strip_json_fence(getattr(response, "text", "") or "")
    if not raw:
        raise RuntimeError("La IA no generó ninguna respuesta.")

    subject_out = existing_subject
    body_out = ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            subject_out = str(data.get("subject") or existing_subject).strip()
            body_out = str(data.get("body") or "").strip()
    except (ValueError, TypeError):
        body_out = raw  # el modelo no devolvió JSON: usar todo como cuerpo

    if not body_out:
        raise RuntimeError("La IA no generó ningún contenido.")
    return {"subject": subject_out or "(sin asunto)", "body": body_out}
