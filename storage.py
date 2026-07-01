"""Structured audit log backed by SQLite.

Every attribution decision and every appeal is recorded here. Milestone 3 stores
the decision fields; Milestone 4 adds the stylometry score and combined score,
and Milestone 5 adds appeal records. The schema below already has room for those
so later milestones only need to populate more columns.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "audit_log.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                content_id   TEXT PRIMARY KEY,
                creator_id   TEXT,
                timestamp    TEXT,
                text         TEXT,
                attribution  TEXT,
                confidence   REAL,
                llm_score    REAL,
                stylometry_score REAL,   -- populated in M4
                ai_score     REAL,       -- combined score, populated in M4
                signals_json TEXT,       -- raw per-signal detail
                status       TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appeals (
                appeal_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id TEXT,
                timestamp  TEXT,
                reason     TEXT
            )
            """
        )


def now_iso() -> str:
    """UTC timestamp, e.g. 2026-06-30T14:32:10.123Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_decision(entry: dict) -> None:
    """Insert an attribution decision. `entry['signals']` (a dict) is stored as JSON."""
    signals_json = json.dumps(entry.get("signals", {}))
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO decisions (
                content_id, creator_id, timestamp, text, attribution,
                confidence, llm_score, stylometry_score, ai_score,
                signals_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["content_id"],
                entry.get("creator_id"),
                entry.get("timestamp", now_iso()),
                entry.get("text"),
                entry.get("attribution"),
                entry.get("confidence"),
                entry.get("llm_score"),
                entry.get("stylometry_score"),
                entry.get("ai_score"),
                signals_json,
                entry.get("status", "classified"),
            ),
        )


def add_appeal(content_id: str, reason: str) -> bool:
    """Record an appeal: move the decision to 'under_review' and log the reason.

    Returns False if no decision exists for content_id (caller returns 404).
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE decisions SET status = 'under_review' WHERE content_id = ?",
            (content_id,),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO appeals (content_id, timestamp, reason) VALUES (?, ?, ?)",
            (content_id, now_iso(), reason),
        )
    return True


def get_decision(content_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE content_id = ?", (content_id,)
        ).fetchone()
    return _row_to_entry(row) if row else None


def get_log(limit: int = 50) -> list[dict]:
    """Most recent decisions first, with any appeals attached."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        entries = [_row_to_entry(r) for r in rows]
        for entry in entries:
            appeals = conn.execute(
                "SELECT timestamp, reason FROM appeals WHERE content_id = ? ORDER BY timestamp",
                (entry["content_id"],),
            ).fetchall()
            entry["appeals"] = [dict(a) for a in appeals]
            entry["appeal_filed"] = bool(appeals)
            # Convenience fields for the most recent appeal.
            entry["appeal_reasoning"] = appeals[-1]["reason"] if appeals else None
    return entries


def _row_to_entry(row: sqlite3.Row) -> dict:
    entry = dict(row)
    if entry.get("signals_json"):
        try:
            entry["signals"] = json.loads(entry["signals_json"])
        except json.JSONDecodeError:
            entry["signals"] = {}
    entry.pop("signals_json", None)
    return entry
