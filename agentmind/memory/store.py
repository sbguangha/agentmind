"""Long-term memory storage: a small SQLite-backed store.

Everything is persisted to a single file (``data/memory.db``) so memories
survive restarts — the agent genuinely remembers across sessions.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);
"""


@dataclass
class MemoryEntry:
    id: str
    kind: str  # "episode" | "fact" | ...
    content: str
    created_at: float
    embedding: list[float] | None = None


class MemoryStore:
    """Async-safe wrapper around a SQLite table."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.executescript(_SCHEMA)
        return conn

    async def add(
        self, content: str, kind: str = "episode", embedding: list[float] | None = None
    ) -> MemoryEntry:
        entry = MemoryEntry(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            content=content,
            created_at=time.time(),
            embedding=embedding,
        )

        def _write() -> None:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO memory_entries (id, kind, content, embedding, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        entry.id,
                        entry.kind,
                        entry.content,
                        json.dumps(embedding) if embedding else None,
                        entry.created_at,
                    ),
                )

        await asyncio.to_thread(_write)
        return entry

    async def all(self) -> list[MemoryEntry]:
        def _read() -> list[MemoryEntry]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, kind, content, embedding, created_at FROM memory_entries"
                    " ORDER BY created_at"
                ).fetchall()
            return [
                MemoryEntry(
                    id=row[0],
                    kind=row[1],
                    content=row[2],
                    created_at=row[4],
                    embedding=json.loads(row[3]) if row[3] else None,
                )
                for row in rows
            ]

        return await asyncio.to_thread(_read)

    async def delete(self, entry_id: str) -> bool:
        def _delete() -> bool:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
                return cur.rowcount > 0

        return await asyncio.to_thread(_delete)

    async def clear(self) -> int:
        def _clear() -> int:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM memory_entries")
                return cur.rowcount

        return await asyncio.to_thread(_clear)
