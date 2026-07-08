"""Tiny JSON-backed personal address book.

Used by the Calendar mode to let the user pick "one of my contacts" as an
event attendee without wiring up the Google People API / an extra OAuth
scope. Contacts are just name+email pairs, persisted to
``config/contacts.json`` (see actions.paths), and are grown automatically
whenever the user adds a brand-new attendee email from the event modal.
"""
from __future__ import annotations

import json
import re
import threading

from actions.paths import config_path

_FILE = config_path("contacts.json")
_lock = threading.RLock()
_cache: list[dict] | None = None

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(str(email or "").strip()))


def _load_locked() -> list[dict]:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, list) else []
        except Exception:
            _cache = []
    return _cache


def _save_locked() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(_cache or [], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def list_contacts() -> list[dict]:
    """All saved contacts, sorted by name then email."""
    with _lock:
        contacts = list(_load_locked())
    return sorted(contacts, key=lambda c: (c.get("name") or "", c.get("email") or ""))


def search_contacts(query: str) -> list[dict]:
    q = str(query or "").strip().lower()
    if not q:
        return list_contacts()
    return [
        c for c in list_contacts()
        if q in str(c.get("name") or "").lower() or q in str(c.get("email") or "").lower()
    ]


def find_contact(email: str) -> dict | None:
    email = str(email or "").strip().lower()
    for c in list_contacts():
        if str(c.get("email") or "").strip().lower() == email:
            return c
    return None


def upsert_contact(name: str, email: str) -> dict:
    """Add a contact, or update its name if the email already exists."""
    email = str(email or "").strip()
    name = str(name or "").strip()
    if not is_valid_email(email):
        raise ValueError(f"Email inválido: {email!r}")
    with _lock:
        contacts = _load_locked()
        for c in contacts:
            if str(c.get("email") or "").strip().lower() == email.lower():
                if name:
                    c["name"] = name
                _save_locked()
                return c
        contact = {"name": name or email, "email": email}
        contacts.append(contact)
        _save_locked()
        return contact


def remove_contact(email: str) -> bool:
    email = str(email or "").strip().lower()
    with _lock:
        contacts = _load_locked()
        kept = [c for c in contacts if str(c.get("email") or "").strip().lower() != email]
        removed = len(kept) != len(contacts)
        if removed:
            _cache_replace(kept)
            _save_locked()
        return removed


def _cache_replace(new_list: list[dict]) -> None:
    global _cache
    _cache = new_list
