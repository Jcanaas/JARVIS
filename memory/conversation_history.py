"""Historial de conversación persistente entre sesiones.

Guarda cada turno (usuario + Jarvis) en memory/conversation_history.json.
Límites:
  - MAX_STORED = 300  turnos en disco
  - MAX_INJECT = 25   turnos inyectados en el prompt
  - MAX_CHARS  = 2000 chars del bloque inyectado (para no saturar el contexto)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import List, Dict

from actions.paths import memory_path

HISTORY_PATH = memory_path("conversation_history.json")
_lock        = Lock()

MAX_STORED = 300
MAX_INJECT = 25
MAX_CHARS  = 2000


# ──────────────────────────────────────────────
# I/O
# ──────────────────────────────────────────────

def load_history() -> List[Dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_turn(user_text: str, jarvis_text: str) -> None:
    """Añade un turno al historial. Llámalo una vez por turn_complete."""
    u = (user_text   or "").strip()[:400]
    j = (jarvis_text or "").strip()[:400]
    if not u and not j:
        return
    entry: Dict = {
        "ts":     datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "user":   u,
        "jarvis": j,
    }
    with _lock:
        history = load_history()
        history.append(entry)
        if len(history) > MAX_STORED:
            history = history[-MAX_STORED:]
        HISTORY_PATH.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_recent(n: int = MAX_INJECT) -> List[Dict]:
    history = load_history()
    return history[-n:] if history else []


def clear_history() -> None:
    """Borra todo el historial."""
    with _lock:
        HISTORY_PATH.write_text("[]", encoding="utf-8")


# ──────────────────────────────────────────────
# Formateo para el system prompt
# ──────────────────────────────────────────────

def _fmt_ts(ts: str) -> str:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        return f"{dt.day} {dt.strftime('%b')} {dt.strftime('%H:%M')}"
    except Exception:
        return ts[:10]


def format_for_prompt(turns: List[Dict] | None = None) -> str:
    """Devuelve un bloque de texto listo para incluir en el system prompt."""
    if turns is None:
        turns = load_recent()
    if not turns:
        return ""

    lines = ["[RECENT CONVERSATION HISTORY — use this to maintain context across sessions]"]
    for t in turns:
        ts  = _fmt_ts(t.get("ts", ""))
        u   = t.get("user",   "").strip()
        j   = t.get("jarvis", "").strip()
        if u:
            lines.append(f"[{ts}] You: {u}")
        if j:
            lines.append(f"[{ts}] Jarvis: {j}")

    full = "\n".join(lines)

    # Si supera el límite de chars, eliminar los turnos más antiguos
    while len(full) > MAX_CHARS and len(lines) > 2:
        # lines[0] es la cabecera; eliminar el primer par de turnos (línea 1 y 2)
        lines.pop(1)
        if len(lines) > 1:
            lines.pop(1)
        full = "\n".join(lines)

    return full
