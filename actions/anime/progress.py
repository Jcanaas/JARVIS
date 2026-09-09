"""Local watch history: positions, completion, and "continue watching".

Nothing in the app persisted playback progress before this — the anime panel
had no memory of where you were in an episode across restarts, no idea which
episodes you'd already seen, and no way to resume. This is a small SQLite
store under memory_path() that fixes that.

`media_key` is deliberately generic ("kitsu:46474", not just a bare kitsu id)
so Movies/TV can reuse this store later without a schema change — see
NOTE_anime_shiru_plan.md phase 1.1. Anime always uses "kitsu:<kitsu_id>".

Writes are cheap and frequent (the player calls save() every ~10s), so this
uses a single connection with WAL journaling rather than opening/closing a
connection per call.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional

from actions.paths import memory_path

_DB_PATH = memory_path("anime_progress.sqlite3")
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

# An episode counts as "watched" past this fraction of its duration — matches
# the auto-complete threshold used by phase 1.4, so a completed episode and a
# "seen" badge always agree.
COMPLETED_FRACTION = 0.85


def kitsu_key(kitsu_id: str) -> str:
    """Build the media_key for an anime, from a bare or "kitsu:"-prefixed id."""
    return f"kitsu:{str(kitsu_id).replace('kitsu:', '')}"


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_entries (
                media_key   TEXT NOT NULL,
                episode     INTEGER NOT NULL,
                position_s  REAL NOT NULL DEFAULT 0,
                duration_s  REAL NOT NULL DEFAULT 0,
                completed   INTEGER NOT NULL DEFAULT 0,
                updated_at  REAL NOT NULL,
                title       TEXT NOT NULL DEFAULT '',
                poster_url  TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (media_key, episode)
            )
        """)
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watch_updated ON watch_entries(updated_at DESC)"
        )
        _conn.commit()
    return _conn


@dataclass
class WatchEntry:
    media_key: str
    episode: int
    position_s: float = 0.0
    duration_s: float = 0.0
    completed: bool = False
    updated_at: float = 0.0
    title: str = ""
    poster_url: str = ""

    @property
    def fraction(self) -> float:
        return (self.position_s / self.duration_s) if self.duration_s > 0 else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fraction"] = self.fraction
        return d


def _row_to_entry(row) -> WatchEntry:
    return WatchEntry(
        media_key=row[0], episode=row[1], position_s=row[2], duration_s=row[3],
        completed=bool(row[4]), updated_at=row[5], title=row[6], poster_url=row[7],
    )


_COLUMNS = "media_key, episode, position_s, duration_s, completed, updated_at, title, poster_url"


def save(media_key: str, episode: int, position_s: float, duration_s: float = 0.0,
         title: str = "", poster_url: str = "", *, autocomplete: bool = True) -> WatchEntry:
    """Upsert one episode's progress, optionally marking it complete at 85%.

    Once an episode is marked completed it stays completed even if called
    again with an earlier position (e.g. the user rewatches the first few
    minutes) — completion should not flicker off from ordinary seeking.
    """
    position_s = max(0.0, float(position_s))
    duration_s = max(0.0, float(duration_s))
    completed = autocomplete and duration_s > 0 and (position_s / duration_s) >= COMPLETED_FRACTION
    now = time.time()

    with _lock:
        conn = _get_conn()
        existing = conn.execute(
            "SELECT completed, title, poster_url FROM watch_entries WHERE media_key=? AND episode=?",
            (media_key, episode),
        ).fetchone()
        if existing:
            completed = completed or bool(existing[0])
            title = title or existing[1]
            poster_url = poster_url or existing[2]
        conn.execute(
            """INSERT INTO watch_entries (media_key, episode, position_s, duration_s,
                                          completed, updated_at, title, poster_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(media_key, episode) DO UPDATE SET
                   position_s=excluded.position_s, duration_s=excluded.duration_s,
                   completed=excluded.completed, updated_at=excluded.updated_at,
                   title=CASE WHEN excluded.title != '' THEN excluded.title ELSE watch_entries.title END,
                   poster_url=CASE WHEN excluded.poster_url != '' THEN excluded.poster_url ELSE watch_entries.poster_url END
            """,
            (media_key, episode, position_s, duration_s, int(completed), now, title, poster_url),
        )
        conn.commit()

    return WatchEntry(media_key, episode, position_s, duration_s, completed, now, title, poster_url)


def mark_completed(media_key: str, episode: int, title: str = "", poster_url: str = "") -> None:
    """Force-complete an episode without a real position (e.g. manual "seen")."""
    with _lock:
        conn = _get_conn()
        now = time.time()
        conn.execute(
            """INSERT INTO watch_entries (media_key, episode, position_s, duration_s,
                                          completed, updated_at, title, poster_url)
               VALUES (?, ?, 0, 0, 1, ?, ?, ?)
               ON CONFLICT(media_key, episode) DO UPDATE SET
                   completed=1, updated_at=excluded.updated_at,
                   title=CASE WHEN excluded.title != '' THEN excluded.title ELSE watch_entries.title END,
                   poster_url=CASE WHEN excluded.poster_url != '' THEN excluded.poster_url ELSE watch_entries.poster_url END
            """,
            (media_key, episode, now, title, poster_url),
        )
        conn.commit()


def get(media_key: str, episode: int) -> Optional[WatchEntry]:
    conn = _get_conn()
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM watch_entries WHERE media_key=? AND episode=?",
        (media_key, episode),
    ).fetchone()
    return _row_to_entry(row) if row else None


def episode_states(media_key: str) -> dict[int, str]:
    """{episode: 'seen' | 'partial'} for every episode with any progress.

    Episodes with no row at all are 'new' by omission — callers treat a
    missing key as new rather than this returning an explicit 'new' entry for
    every episode in a series, which would mean querying the full episode
    list just to build this dict.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT episode, completed FROM watch_entries WHERE media_key=?",
        (media_key,),
    ).fetchall()
    return {ep: ("seen" if completed else "partial") for ep, completed in rows}


def latest_episode(media_key: str) -> Optional[WatchEntry]:
    """Most recently touched episode for one series, for a "resume" action."""
    conn = _get_conn()
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM watch_entries WHERE media_key=? ORDER BY updated_at DESC LIMIT 1",
        (media_key,),
    ).fetchone()
    return _row_to_entry(row) if row else None


def continue_watching(limit: int = 20) -> list[WatchEntry]:
    """Most recently touched, unfinished episode per series — the home rail.

    "Unfinished" means either not completed, or completed but with a newer
    sibling episode already in progress (so finishing episode 3 doesn't
    orphan the rail entry — it should point at 4 once you start it, or
    disappear if you haven't). Filtering that in SQL would need a window
    function; doing it in Python over "one row per series" is simpler and this
    table is small (per-episode rows for actively watched shows only).
    """
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM watch_entries ORDER BY updated_at DESC"
    ).fetchall()

    seen_series: set[str] = set()
    out: list[WatchEntry] = []
    for row in rows:
        entry = _row_to_entry(row)
        if entry.media_key in seen_series:
            continue
        seen_series.add(entry.media_key)
        if not entry.completed:
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def delete_series(media_key: str) -> None:
    """Drop every entry for one series (e.g. "remove from continue watching")."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM watch_entries WHERE media_key=?", (media_key,))
        conn.commit()
