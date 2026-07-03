"""Persistent auto-reply rules for WhatsApp.

A rule describes *when* and *for whom* Jarvis should answer WhatsApp messages
automatically, plus the prompt that drives the AI reply. Rules are stored as a
JSON list in ``config/whatsapp_rules.json``; their order in the list is their
priority (first match wins). The store is thread-safe and reloads itself when
the file changes on disk, so the running ``WhatsAppManager`` always sees edits
made from the settings UI without a restart.

Rule shape::

    {
      "id": "uuid4",
      "name": "Fuera de oficina",
      "enabled": True,
      "contacts": [{"chat_id": "...@c.us", "name": "Juan"}],
      "always": False,          # if True, days/start/end are ignored
      "days": [0, 1, 2, 3, 4],  # 0=Mon … 6=Sun
      "start": "09:00",
      "end": "18:00",
      "timezone": "Europe/Madrid",
      "prompt": "Responde que estoy reunido…"
    }
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

from actions.paths import config_path

_FILE = config_path("whatsapp_rules.json")
_lock = threading.RLock()
_cache: List[Dict[str, Any]] | None = None
_mtime: float = 0.0


# ── persistence ─────────────────────────────────────────────────────────────
def _file_mtime() -> float:
    try:
        return _FILE.stat().st_mtime
    except OSError:
        return 0.0


def _load_locked() -> List[Dict[str, Any]]:
    """Return the cached rules, reloading from disk if the file changed."""
    global _cache, _mtime
    mtime = _file_mtime()
    if _cache is None or mtime != _mtime:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _cache = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        except Exception:
            _cache = []
        _mtime = mtime
    return _cache


def _save_locked(rules: List[Dict[str, Any]]) -> None:
    global _cache, _mtime
    _cache = rules
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
        _mtime = _file_mtime()
    except Exception:
        pass


def load_rules() -> List[Dict[str, Any]]:
    """Return a copy of every stored rule, in priority order."""
    with _lock:
        return [dict(r) for r in _load_locked()]


def save_rules(rules: List[Dict[str, Any]]) -> None:
    """Replace the whole rule list (order defines priority)."""
    with _lock:
        _save_locked([dict(r) for r in rules])


def _normalize(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in defaults / coerce types for a rule coming from the UI."""
    contacts = []
    for c in rule.get("contacts") or []:
        chat_id = str(c.get("chat_id") or "").strip()
        if chat_id:
            contacts.append({"chat_id": chat_id, "name": str(c.get("name") or chat_id)})
    days = sorted({int(d) for d in (rule.get("days") or []) if 0 <= int(d) <= 6})
    return {
        "id": str(rule.get("id") or uuid.uuid4().hex),
        "name": str(rule.get("name") or "Regla sin nombre").strip(),
        "enabled": bool(rule.get("enabled", True)),
        "contacts": contacts,
        "always": bool(rule.get("always", False)),
        "days": days,
        "start": str(rule.get("start") or "00:00"),
        "end": str(rule.get("end") or "23:59"),
        "timezone": str(rule.get("timezone") or "").strip(),
        "prompt": str(rule.get("prompt") or "").strip(),
    }


def add_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Append a new rule (lowest priority) and persist. Returns the stored rule."""
    stored = _normalize(rule)
    with _lock:
        rules = list(_load_locked())
        rules.append(stored)
        _save_locked(rules)
    return dict(stored)


def update_rule(rule_id: str, rule: Dict[str, Any]) -> bool:
    """Replace the rule with `rule_id` in place. Returns True if found."""
    merged = _normalize({**rule, "id": rule_id})
    with _lock:
        rules = list(_load_locked())
        for i, r in enumerate(rules):
            if r.get("id") == rule_id:
                rules[i] = merged
                _save_locked(rules)
                return True
    return False


def delete_rule(rule_id: str) -> bool:
    with _lock:
        rules = list(_load_locked())
        kept = [r for r in rules if r.get("id") != rule_id]
        if len(kept) == len(rules):
            return False
        _save_locked(kept)
        return True


def move_rule(rule_id: str, delta: int) -> bool:
    """Shift a rule up (delta<0) or down (delta>0) to change its priority."""
    with _lock:
        rules = list(_load_locked())
        idx = next((i for i, r in enumerate(rules) if r.get("id") == rule_id), -1)
        if idx < 0:
            return False
        new_idx = max(0, min(len(rules) - 1, idx + delta))
        if new_idx == idx:
            return False
        rules.insert(new_idx, rules.pop(idx))
        _save_locked(rules)
        return True


# ── matching ────────────────────────────────────────────────────────────────
def _parse_hhmm(value: str) -> Optional[dtime]:
    try:
        hh, mm = str(value).split(":")[:2]
        return dtime(int(hh), int(mm))
    except Exception:
        return None


def _now_in_tz(timezone: str) -> datetime:
    """Current local time in the rule's timezone (falls back to system local)."""
    if timezone:
        try:
            from dateutil import tz
            zone = tz.gettz(timezone)
            if zone is not None:
                return datetime.now(zone)
        except Exception:
            pass
    return datetime.now().astimezone()


def _in_schedule(rule: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    if rule.get("always"):
        return True
    now = now or _now_in_tz(str(rule.get("timezone") or ""))
    days = rule.get("days") or []
    if days and now.weekday() not in days:
        return False
    start = _parse_hhmm(str(rule.get("start") or "00:00")) or dtime(0, 0)
    end = _parse_hhmm(str(rule.get("end") or "23:59")) or dtime(23, 59)
    current = now.time()
    if start <= end:
        return start <= current <= end
    # Window crosses midnight (e.g. 22:00 → 06:00).
    return current >= start or current <= end


def match_rule(chat_id: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Return the first enabled rule that covers `chat_id` right now, else None."""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return None
    with _lock:
        rules = list(_load_locked())
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if not any(c.get("chat_id") == chat_id for c in rule.get("contacts") or []):
            continue
        rule_now = now or _now_in_tz(str(rule.get("timezone") or ""))
        if _in_schedule(rule, rule_now):
            return dict(rule)
    return None
