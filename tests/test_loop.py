"""Tests for the AgentLoop orchestration and session persistence."""
from __future__ import annotations

import asyncio

from agentmind.bus.queue import InboundMessage, MessageBus
from agentmind.core.loop import AgentLoop
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.manager import SessionManager
from agentmind.session.types import Message, Session
from agentmind.tools.registry import ToolRegistry
from tests.conftest import EchoTool, make_settings
from tests.test_runner import MockProvider


async def _make_stack(tmp_path, script):
    settings = make_settings(tmp_path, memory_auto_store=True)
    provider = MockProvider(script)
    registry = ToolRegistry()
    registry.register(EchoTool())
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    sessions = SessionManager(settings.resolved_data_dir)
    from agentmind.core.runner import AgentRunner

    runner = AgentRunner(provider, registry, memory, settings)
    return settings, provider, memory, sessions, runner


async def _collect_until_terminal(bus: MessageBus, outbound: list):
    """Consume outbound events until a terminal (done/error) event arrives."""
    while True:
        msg = await bus.receive_outbound()
        outbound.append(msg)
        if msg.event in {"done", "error"}:
            return


async def test_loop_end_to_end(tmp_path):
    """inbound -> loop -> outbound(delta/done) + history persisted + memory stored."""
    bus = MessageBus()
    settings, provider, memory, sessions, runner = await _make_stack(tmp_path, ["最终答案"])

    loop = AgentLoop(bus, runner, sessions, memory, settings)
    outbound: list = []

    loop_task = asyncio.create_task(loop.run())
    try:
        await bus.publish(InboundMessage(session_id="sess-1", text="你好"))
        await asyncio.wait_for(_collect_until_terminal(bus, outbound), timeout=5)
    finally:
        loop.stop()
        await asyncio.wait_for(asyncio.shield(loop_task), timeout=5)

    events = [m.event for m in outbound]
    assert "delta" in events
    assert events[-1] == "done"
    assert outbound[-1].payload["answer"] == "最终答案"
    assert all(m.session_id == "sess-1" for m in outbound)

    # history persisted
    session = sessions.get("sess-1")
    assert session is not None
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert session.messages[-1].content == "最终答案"

    # long-term memory consolidated (auto_store enabled)
    entries = await memory.all()
    assert any("最终答案" in e.content for e in entries)


async def test_loop_emits_error_on_failure(tmp_path):
    class BoomProvider(MockProvider):
        async def stream(self, messages, model, *, tools=None, temperature=0.7):
            raise RuntimeError("provider down")
            yield  # pragma: no cover - makes stream an async generator

    bus = MessageBus()
    settings = make_settings(tmp_path)
    provider = BoomProvider([])
    registry = ToolRegistry()
    registry.register(EchoTool())
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    sessions = SessionManager(settings.resolved_data_dir)
    from agentmind.core.runner import AgentRunner

    loop = AgentLoop(bus, AgentRunner(provider, registry, memory, settings), sessions, memory, settings)

    outbound = []

    async def collect():
        await _collect_until_terminal(bus, outbound)

    loop_task = asyncio.create_task(loop.run())
    try:
        await bus.publish(InboundMessage(session_id="sess-2", text="hi"))
        await asyncio.wait_for(collect(), timeout=5)
    finally:
        loop.stop()
        await asyncio.wait_for(asyncio.shield(loop_task), timeout=5)
    assert outbound[-1].event == "error"
    assert "provider down" in outbound[-1].payload["message"]


async def test_session_persistence_across_reload(tmp_path):
    settings = make_settings(tmp_path)
    manager = SessionManager(settings.resolved_data_dir)
    session = manager.create()
    await manager.append(session, Message(role="user", content="第一条"))
    await manager.append(session, Message(role="assistant", content="回复"))

    # reload from disk
    manager2 = SessionManager(settings.resolved_data_dir)
    reloaded = manager2.get(session.id)
    assert reloaded is not None
    assert [m.role for m in reloaded.messages] == ["user", "assistant"]
    assert reloaded.title == "第一条"


def test_context_window_trims_old_history(tmp_path):
    manager = SessionManager(tmp_path / "data")
    session = Session(id="s")
    for i in range(10):
        session.messages.append(Message(role="user", content="x" * 100))
    window = manager.context_window(session, max_chars=300)
    assert len(window) < 10  # trimmed to fit budget
    assert len("".join(m.content for m in window)) <= 300
