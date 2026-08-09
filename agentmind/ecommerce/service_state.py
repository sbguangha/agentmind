"""Customer-service session state machine.

    new (待处理) ──工具介入──▶ processing (处理中)
    processing ──resolve────▶ resolved (已解决)
    processing ──escalate───▶ escalated (已转人工)
    processing ──超时自动────▶ escalated (超时升级)

Terminal states (resolved/escalated) never regress. State is persisted on the
Session itself (``service_state``), so it survives restarts.
"""
from __future__ import annotations

import time as _time
from datetime import datetime

from agentmind.session.manager import SessionManager

STATE_LABELS = {"new": "待处理", "processing": "处理中", "resolved": "已解决", "escalated": "已转人工"}
_TERMINAL = frozenset({"resolved", "escalated"})


class ServiceSessionTracker:
    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions

    @staticmethod
    def label(state: str | None) -> str:
        return STATE_LABELS.get(state or "new", state or "待处理")

    async def note_activity(self, session_id: str) -> str | None:
        """Any service tool use moves a session into 处理中 (no-op on terminal states)."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.service_state in _TERMINAL:
            return session.service_state
        if session.service_state != "processing":
            session.service_state = "processing"
            await self._sessions.save(session)
        return session.service_state

    async def escalate(self, session_id: str, reason: str, now: datetime | None = None) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {"error": f"会话不存在: {session_id}"}
        now = now or datetime.now()
        ticket = f"TS{now:%Y%m%d%H%M%S}"
        session.service_state = "escalated"
        await self._sessions.save(session)
        return {
            "session_id": session_id,
            "ticket_id": ticket,
            "reason": reason,
            "created_at": now.strftime("%Y-%m-%d %H:%M"),
        }

    async def resolve(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {"error": f"会话不存在: {session_id}"}
        session.service_state = "resolved"
        await self._sessions.save(session)
        return {"session_id": session_id, "resolved_at": datetime.now().strftime("%Y-%m-%d %H:%M")}

    async def check_timeout(self, timeout_minutes: int, now: float | None = None) -> list[dict]:
        """Escalate sessions stuck in 处理中 past the timeout (automation)."""
        now = now or _time.time()
        escalations: list[dict] = []
        for session in self._sessions.all_sessions():
            if session.service_state == "processing" and (now - session.updated_at) > timeout_minutes * 60:
                session.service_state = "escalated"
                await self._sessions.save(session)
                escalations.append({"session_id": session.id, "reason": "处理超时自动升级人工客服"})
        return escalations
