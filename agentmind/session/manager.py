"""Session manager: create, load, persist and trim conversations.

Sessions are persisted to ``data/sessions/*.json`` with atomic writes
(temp file + rename), so history survives restarts. Context trimming keeps
the short-term memory budget bounded.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

from agentmind.session.types import Message, Session


class SessionManager:
    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._load()

    # ---- persistence ---------------------------------------------------
    def _load(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                data = _read_json(path)
                session = Session.from_dict(data)
                self._sessions[session.id] = session
            except Exception:  # noqa: BLE001 - skip corrupt files
                continue

    async def _save(self, session: Session) -> None:
        payload = _json_bytes(session.to_dict())

        def _write() -> None:
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.replace(tmp, self._dir / f"{session.id}.json")

        await asyncio.to_thread(_write)

    async def save(self, session: Session) -> None:
        """Persist a session (used by compression/consolidation writers)."""
        await self._save(session)

    # ---- CRUD ----------------------------------------------------------
    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        session = Session(id=session_id or Session.new_id())
        self._sessions[session.id] = session
        return session

    def create(self) -> Session:
        return self.get_or_create(None)

    def delete(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        path = self._dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
        return True

    def list(self) -> list[dict]:
        return [
            {
                "id": s.id,
                "title": s.title,
                "message_count": len(s.messages),
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "access_mode": s.access_mode,
                "service_state": s.service_state,
            }
            for s in sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        ]

    # ---- history operations -------------------------------------------
    async def append(self, session: Session, message: Message) -> None:
        session.messages.append(message)
        session.updated_at = time.time()
        if session.title == "新对话" and message.role == "user" and message.content.strip():
            session.title = message.content[:30].replace("\n", " ")
        await self._save(session)

    def context_window(self, session: Session, max_chars: int) -> list[Message]:
        """Delegate to :meth:`Session.context_window`."""
        return session.context_window(max_chars)


def _read_json(path: Path) -> dict:
    import json

    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _json_bytes(data: dict) -> bytes:
    import json

    return json.dumps(data, ensure_ascii=False).encode("utf-8")
