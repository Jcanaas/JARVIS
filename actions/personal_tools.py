from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import pyperclip
    _PYPERCLIP = True
except Exception:
    pyperclip = None
    _PYPERCLIP = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


from actions.paths import RESOURCE_DIR, MEMORY_DIR, memory_path
BASE_DIR = RESOURCE_DIR
NOTES_FILE = memory_path("notes.json")
CLIPBOARD_FILE = MEMORY_DIR / "clipboard_history.json"
MAX_CLIPBOARD_ITEMS = 25


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_json(path: Path, fallback: Any):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return fallback


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _memory_entries() -> list[dict]:
    from memory.memory_manager import load_memory

    memory = load_memory()
    entries: list[dict] = []
    for category, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            value = entry.get("value") if isinstance(entry, dict) else entry
            updated = entry.get("updated") if isinstance(entry, dict) else ""
            if value:
                entries.append({
                    "category": category,
                    "key": key,
                    "value": str(value),
                    "updated": updated or "",
                })
    return entries


def memory_list(category: str = "", limit: int = 50) -> list[dict]:
    entries = _memory_entries()
    if category:
        entries = [e for e in entries if e["category"] == category]
    entries.sort(key=lambda e: e.get("updated", ""), reverse=True)
    return entries[:max(1, int(limit or 50))]


def memory_search(query: str, category: str = "", limit: int = 20) -> list[dict]:
    q = _normalize(query)
    entries = memory_list(category=category, limit=500)
    if not q:
        return entries[:max(1, int(limit or 20))]
    matches = []
    for entry in entries:
        haystack = _normalize(f"{entry['category']} {entry['key']} {entry['value']}")
        if q in haystack:
            matches.append(entry)
    return matches[:max(1, int(limit or 20))]


def memory_forget(key: str = "", category: str = "", query: str = "") -> str:
    from memory.memory_manager import forget, load_memory, save_memory

    if key and category:
        return forget(key=key, category=category)

    needle = _normalize(key or query)
    if not needle:
        return "Necesito una key, categoria o texto para borrar memoria."

    memory = load_memory()
    removed = []
    for cat, items in list(memory.items()):
        if category and cat != category:
            continue
        if not isinstance(items, dict):
            continue
        for item_key, entry in list(items.items()):
            value = entry.get("value") if isinstance(entry, dict) else entry
            haystack = _normalize(f"{cat} {item_key} {value}")
            if needle in haystack:
                del items[item_key]
                removed.append(f"{cat}/{item_key}")
    if removed:
        save_memory(memory)
        return "Memoria borrada: " + ", ".join(removed)
    return "No he encontrado memoria que coincida."


def _load_notes() -> list[dict]:
    data = _load_json(NOTES_FILE, [])
    return data if isinstance(data, list) else []


def notes_add(text: str, title: str = "", tags=None) -> dict:
    notes = _load_notes()
    note = {
        "id": f"note_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
        "title": str(title or "").strip() or str(text or "").strip()[:60] or "Nota",
        "text": str(text or "").strip(),
        "tags": tags if isinstance(tags, list) else [],
        "created": _now(),
    }
    if not note["text"]:
        return {"error": "Necesito texto para guardar la nota."}
    notes.append(note)
    _save_json(NOTES_FILE, notes)
    return note


def notes_list(limit: int = 20) -> list[dict]:
    notes = _load_notes()
    notes.sort(key=lambda n: n.get("created", ""), reverse=True)
    return notes[:max(1, int(limit or 20))]


def notes_search(query: str, limit: int = 20) -> list[dict]:
    q = _normalize(query)
    if not q:
        return notes_list(limit)
    matches = []
    for note in notes_list(500):
        haystack = _normalize(f"{note.get('title', '')} {note.get('text', '')} {' '.join(note.get('tags', []))}")
        if q in haystack:
            matches.append(note)
    return matches[:max(1, int(limit or 20))]


def _load_clipboard_history() -> list[dict]:
    data = _load_json(CLIPBOARD_FILE, [])
    return data if isinstance(data, list) else []


def _save_clipboard_item(text: str) -> None:
    value = str(text or "")
    if not value:
        return
    items = [i for i in _load_clipboard_history() if i.get("text") != value]
    items.insert(0, {"text": value, "created": _now()})
    _save_json(CLIPBOARD_FILE, items[:MAX_CLIPBOARD_ITEMS])


def clipboard_get() -> str:
    if not _PYPERCLIP:
        return "pyperclip no esta instalado."
    text = pyperclip.paste()
    _save_clipboard_item(text)
    return text


def clipboard_set(text: str) -> str:
    if not _PYPERCLIP:
        return "pyperclip no esta instalado."
    value = str(text or "")
    pyperclip.copy(value)
    _save_clipboard_item(value)
    return "Texto copiado al portapapeles."


def clipboard_history(limit: int = 10) -> list[dict]:
    return _load_clipboard_history()[:max(1, int(limit or 10))]


def personal_tools(parameters: dict, player=None, speak=None):
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()

    if player:
        player.write_log(f"[Personal] {action}")

    if action == "memory_list":
        return memory_list(category=params.get("category", ""), limit=int(params.get("limit") or 50))
    if action == "memory_search":
        return memory_search(
            query=params.get("query", ""),
            category=params.get("category", ""),
            limit=int(params.get("limit") or 20),
        )
    if action == "memory_forget":
        return memory_forget(
            key=params.get("key", ""),
            category=params.get("category", ""),
            query=params.get("query", ""),
        )
    if action == "notes_add":
        return notes_add(
            text=params.get("text") or params.get("body") or params.get("note") or "",
            title=params.get("title", ""),
            tags=params.get("tags") or [],
        )
    if action == "notes_list":
        return notes_list(limit=int(params.get("limit") or 20))
    if action == "notes_search":
        return notes_search(query=params.get("query", ""), limit=int(params.get("limit") or 20))
    if action == "clipboard_get":
        return clipboard_get()
    if action == "clipboard_set":
        return clipboard_set(params.get("text") or params.get("body") or "")
    if action == "clipboard_history":
        return clipboard_history(limit=int(params.get("limit") or 10))

    return (
        "Accion desconocida. Usa memory_list, memory_search, memory_forget, "
        "notes_add, notes_list, notes_search, clipboard_get, clipboard_set o clipboard_history."
    )
