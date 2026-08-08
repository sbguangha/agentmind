"""AgentLoop — the orchestration layer.

Listens on the message bus and drives the full agent pipeline for every
inbound message:

    inbound -> (per-session lock) -> run ReAct turn -> persist history
             -> consolidate long-term memory -> publish outbound events

Different sessions run concurrently; messages to the *same* session are
processed sequentially so context never interleaves.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Awaitable

from agentmind.bus.queue import InboundMessage, MessageBus, OutboundMessage
from agentmind.config import Settings
from agentmind.core.compressor import Compressor
from agentmind.core.consolidator import MemoryConsolidator
from agentmind.core.runner import AgentRunner
from agentmind.memory.long_term import LongTermMemory
from agentmind.session.manager import SessionManager
from agentmind.session.types import Message

EmitFn = Callable[[str, dict], Awaitable[None]]


class AgentLoop:
    def __init__(
        self,
        bus: MessageBus,
        runner: AgentRunner,
        sessions: SessionManager,
        memory: LongTermMemory,
        settings: Settings,
        compressor: Compressor | None = None,
        consolidator: MemoryConsolidator | None = None,
    ) -> None:
        self._bus = bus
        self._runner = runner
        self._sessions = sessions
        self._memory = memory
        self._settings = settings
        self._compressor = compressor
        self._consolidator = consolidator
        self._locks: dict[str, asyncio.Lock] = {}
        self._stop = asyncio.Event()

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def run(self) -> None:
        """Consume inbound messages forever (until :meth:`stop`)."""
        while not self._stop.is_set():
            try:
                msg = await asyncio.wait_for(self._bus.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            asyncio.create_task(self._handle(msg))

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    async def _handle(self, msg: InboundMessage) -> None:
        async with self._lock_for(msg.session_id):
            session = self._sessions.get_or_create(msg.session_id)

            async def emit(event: str, payload: dict) -> None:
                await self._bus.publish_outbound(
                    OutboundMessage(session_id=msg.session_id, event=event, payload=payload)
                )

            try:
                # compress short-term history before the turn (old -> summary)
                if self._compressor is not None:
                    compressed = await self._compressor.maybe_compress(session)
                    if compressed:
                        await self._sessions.save(session)

                answer = await self._runner.run_turn(session, msg.text, emit)
                await self._persist_turn(session, msg.text, answer)
                await emit("done", {"answer": answer})
            except Exception as exc:  # noqa: BLE001 - surface failures to the UI
                await emit("error", {"message": str(exc)})

    async def _persist_turn(self, session, user_text: str, answer: str) -> None:
        """Save history (short-term) and consolidate long-term memory."""
        await self._sessions.append(session, Message(role="user", content=user_text))
        await self._sessions.append(session, Message(role="assistant", content=answer))

        if self._settings.memory_auto_store and answer.strip():
            await self._memory.remember(
                f"用户问: {user_text[:200]}\n助手答: {answer[:400]}", kind="episode"
            )
            if self._consolidator is not None:
                await self._consolidator.maybe_consolidate()
