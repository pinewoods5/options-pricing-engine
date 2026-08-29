"""A cache for AI reads, keyed by the fingerprint of the position they describe.

The pricing half of an analysis is free and takes milliseconds, so it is always
recomputed. The AI read is the one part that costs real money and real seconds,
and dragging a slider produces a great many nearly-identical positions -- so it
is stored against engine.structure.fingerprint(), which is deliberately coarse
enough that a small nudge lands on the read that is already there.

SQLite because the cache should outlive a restart: reopening the app and
landing on a position you looked at yesterday should not spend money again.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "convexity.db"

# A read describes a position, and positions do not change their nature. The
# expiry is here because the *prompt* may change as the product develops, and
# an old read written by an older prompt should eventually make way.
MAX_AGE_SECONDS = 30 * 24 * 3600


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reads (
                fingerprint TEXT PRIMARY KEY,
                payload     TEXT NOT NULL,
                created_at  REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                fingerprint TEXT PRIMARY KEY,
                summary     TEXT NOT NULL,
                seen_at     REAL NOT NULL
            )
            """
        )


def get_read(fingerprint: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload, created_at FROM reads WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
    if row is None:
        return None
    if time.time() - row["created_at"] > MAX_AGE_SECONDS:
        return None
    return json.loads(row["payload"])


def save_read(fingerprint: str, payload: dict) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO reads (fingerprint, payload, created_at) "
            "VALUES (?, ?, ?)",
            (fingerprint, json.dumps(payload), time.time()),
        )


def record_history(fingerprint: str, summary: dict) -> None:
    """Remember that this position was looked at, for the History rail item."""
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO history (fingerprint, summary, seen_at) "
            "VALUES (?, ?, ?)",
            (fingerprint, json.dumps(summary), time.time()),
        )


def recent_history(limit: int = 25) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT fingerprint, summary, seen_at FROM history "
            "ORDER BY seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {**json.loads(row["summary"]), "fingerprint": row["fingerprint"],
         "seen_at": row["seen_at"]}
        for row in rows
    ]
