"""MemoryStore — SQLite-backed persistent key-value storage for agent memory."""

from __future__ import annotations

import datetime
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_NAME = "memory.db"


class MemoryStore:
    """Persistent key-value store backed by SQLite.

    Lives at ``~/.harness/memory.db``.  Multiple harness processes can
    safely read/write via WAL mode.
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            base = Path.home() / ".harness"
            base.mkdir(parents=True, exist_ok=True)
            db_path = base / DEFAULT_DB_NAME
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memories (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
        )
        self._db.commit()

    def read(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM memories WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def write(self, key: str, value: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._db.execute(
            "INSERT OR REPLACE INTO memories (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        self._db.commit()

    def delete(self, key: str) -> bool:
        cur = self._db.execute("DELETE FROM memories WHERE key = ?", (key,))
        self._db.commit()
        return cur.rowcount > 0

    def list_keys(self) -> list[str]:
        rows = self._db.execute("SELECT key FROM memories ORDER BY updated_at DESC").fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._db.close()
