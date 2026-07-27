#cardtrader_catalog.py
"""Local SQLite index of CardTrader expansions + blueprints.

The CardTrader API has no search-by-name endpoint: the only path to a
card's blueprints is expansion -> /blueprints/export. This module builds
and maintains a local index so name resolution is instant offline.
"""
from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from actions import event_bus
from actions.cardtrader_api import CardTraderClient
from actions.paths import config_path

DB_PATH = config_path("cardtrader_catalog.db")
SCHEMA_VERSION = "1"

ProgressCB = Callable[[int, int, str], None] | None


@dataclass
class Blueprint:
    id: int
    name: str
    version: str | None
    expansion_id: int
    expansion_code: str
    expansion_name: str
    scryfall_id: str | None
    collector_number: str | None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS expansions (
            id INTEGER PRIMARY KEY,
            game_id INTEGER,
            code TEXT,
            name TEXT,
            synced_at TEXT
        );
        CREATE TABLE IF NOT EXISTS blueprints (
            id INTEGER PRIMARY KEY,
            name TEXT,
            name_norm TEXT,
            version TEXT,
            expansion_id INTEGER REFERENCES expansions(id),
            category_id INTEGER,
            scryfall_id TEXT,
            collector_number TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bp_name_norm ON blueprints(name_norm);
        CREATE INDEX IF NOT EXISTS idx_bp_expansion ON blueprints(expansion_id);
        """
    )
    conn.commit()


def normalize_name(name: str) -> str:
    name = name.strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9\s/]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _split_faces(name: str) -> list[str]:
    """Split a double-faced card name 'A // B' into individual faces."""
    parts = re.split(r"\s*//\s*", name)
    return [p.strip() for p in parts if p.strip()]


def _resolve_mtg_game_id(client: CardTraderClient) -> int:
    games = client.games()
    for g in games:
        if str(g.get("name", "")).strip().lower() in ("magic", "magic: the gathering", "magic the gathering"):
            return int(g["id"])
    # Fallback: documented convention is game_id 1 for MTG.
    return 1


def ensure_catalog(progress_cb: ProgressCB = None) -> dict:
    """Sync if the catalog is empty or has expansions pending blueprint sync."""
    conn = _connect()
    _ensure_schema(conn)
    row = conn.execute("SELECT COUNT(*) AS n FROM expansions").fetchone()
    if row["n"] == 0:
        conn.close()
        return full_resync(progress_cb)
    pending = conn.execute("SELECT COUNT(*) AS n FROM expansions WHERE synced_at IS NULL").fetchone()["n"]
    conn.close()
    if pending:
        return refresh_new_expansions(progress_cb)
    return catalog_status()


def refresh_new_expansions(progress_cb: ProgressCB = None) -> dict:
    """Sync expansions that don't exist locally yet, or exist but never
    got their blueprints downloaded (synced_at IS NULL)."""
    started = time.monotonic()
    client = CardTraderClient()
    conn = _connect()
    _ensure_schema(conn)

    game_id = _resolve_mtg_game_id(client)
    remote_expansions = [e for e in client.expansions() if e.get("game_id") == game_id]

    existing_ids = {r["id"] for r in conn.execute("SELECT id FROM expansions").fetchall()}
    for exp in remote_expansions:
        if exp["id"] not in existing_ids:
            conn.execute(
                "INSERT INTO expansions (id, game_id, code, name, synced_at) VALUES (?, ?, ?, ?, NULL)",
                (exp["id"], exp["game_id"], exp.get("code", ""), exp.get("name", "")),
            )
    conn.commit()

    pending = conn.execute("SELECT id, code, name FROM expansions WHERE synced_at IS NULL").fetchall()
    total = len(pending)
    blueprint_count = 0
    for i, exp in enumerate(pending, start=1):
        if progress_cb:
            progress_cb(i, total, exp["name"])
        event_bus.progress(total, i, f"CardTrader: {exp['name']}")
        try:
            blueprints = client.blueprints_export(exp["id"])
        except Exception as e:
            event_bus.log("CardTrader", f"Fallo blueprints de {exp['name']}: {e}")
            continue
        for bp in blueprints:
            name = bp.get("name", "")
            collector_number = (bp.get("fixed_properties") or {}).get("collector_number")
            conn.execute(
                """INSERT OR REPLACE INTO blueprints
                   (id, name, name_norm, version, expansion_id, category_id, scryfall_id, collector_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bp["id"], name, normalize_name(name), bp.get("version"),
                    exp["id"], bp.get("category_id"), bp.get("scryfall_id"), collector_number,
                ),
            )
            blueprint_count += 1
        conn.execute("UPDATE expansions SET synced_at = ? WHERE id = ?", (str(time.time()), exp["id"]))
        conn.commit()

    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)", (str(time.time()),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('game_id', ?)", (str(game_id),))
    conn.commit()
    conn.close()

    return {
        "expansions_synced": total,
        "blueprints_synced": blueprint_count,
        "seconds": round(time.monotonic() - started, 1),
    }


def full_resync(progress_cb: ProgressCB = None) -> dict:
    conn = _connect()
    conn.executescript("DROP TABLE IF EXISTS blueprints; DROP TABLE IF EXISTS expansions; DROP TABLE IF EXISTS meta;")
    conn.commit()
    conn.close()
    return refresh_new_expansions(progress_cb)


def catalog_status() -> dict:
    conn = _connect()
    _ensure_schema(conn)
    n_exp = conn.execute("SELECT COUNT(*) AS n FROM expansions").fetchone()["n"]
    n_bp = conn.execute("SELECT COUNT(*) AS n FROM blueprints").fetchone()["n"]
    pending = conn.execute("SELECT COUNT(*) AS n FROM expansions WHERE synced_at IS NULL").fetchone()["n"]
    last_sync_row = conn.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
    conn.close()
    return {
        "expansions": n_exp,
        "blueprints": n_bp,
        "pending_expansions": pending,
        "last_sync": last_sync_row["value"] if last_sync_row else None,
    }


def find_blueprints(name: str, set_code: str | None = None) -> list[Blueprint]:
    conn = _connect()
    _ensure_schema(conn)

    target = normalize_name(name)
    candidates = [target] + _split_faces(target)

    query = """
        SELECT b.id, b.name, b.version, b.expansion_id, b.scryfall_id, b.collector_number,
               e.code AS expansion_code, e.name AS expansion_name
        FROM blueprints b JOIN expansions e ON e.id = b.expansion_id
        WHERE b.name_norm = ?
    """
    if set_code:
        query += " AND LOWER(e.code) = ?"

    rows: list[sqlite3.Row] = []
    for cand in candidates:
        params = [cand] + ([set_code.lower()] if set_code else [])
        rows = conn.execute(query, params).fetchall()
        if rows:
            break

    if not rows:
        # Fallback: prefix match on the full (unsplit) target.
        like_query = """
            SELECT b.id, b.name, b.version, b.expansion_id, b.scryfall_id, b.collector_number,
                   e.code AS expansion_code, e.name AS expansion_name
            FROM blueprints b JOIN expansions e ON e.id = b.expansion_id
            WHERE b.name_norm LIKE ?
        """
        like_params = [f"{target}%"]
        if set_code:
            like_query += " AND LOWER(e.code) = ?"
            like_params.append(set_code.lower())
        rows = conn.execute(like_query, like_params).fetchall()

    conn.close()
    return [
        Blueprint(
            id=r["id"], name=r["name"], version=r["version"],
            expansion_id=r["expansion_id"], expansion_code=r["expansion_code"],
            expansion_name=r["expansion_name"], scryfall_id=r["scryfall_id"],
            collector_number=r["collector_number"],
        )
        for r in rows
    ]
