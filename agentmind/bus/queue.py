"""Async message bus decoupling the chat interface from the agent core.

The bus is the backbone of the *perception* pipeline: UI / channels publish
inbound events, the AgentLoop consumes them, and streamed results are
published back as outbound events for the UI to render.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class InboundMessage:
    """A message entering the agent from the outside world (a user, a channel...)."""

    session_id: str
    text: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class OutboundMessage:
    """A streamed piece of agent output routed back to a specific session."""

    session_id: str
    event: str  # "delta" | "tool_start" | "tool_end" | "done" | "error"
    payload: dict
    timestamp: float = field(default_factory=time.time)


class MessageBus:
    """Two unbounded async queues: one inbound, one outbound."""

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    # ---- inbound (world -> agent) -------------------------------------
    async def publish(self, msg: InboundMessage) -> None:
        await self._inbound.put(msg)

    async def receive(self) -> InboundMessage:
        return await self._inbound.get()

    # ---- outbound (agent -> world) ------------------------------------
    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self._outbound.put(msg)

    async def receive_outbound(self) -> OutboundMessage:
        return await self._outbound.get()
