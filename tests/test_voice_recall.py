"""Tests for auto-voice after every answer, user voice upload, and message recall."""
from __future__ import annotations

import asyncio
import base64
import time

import aiohttp

from agentmind.api.server import AgentServer
from agentmind.bus.queue import InboundMessage, MessageBus
from agentmind.core.loop import AgentLoop
from agentmind.core.runner import AgentRunner
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.runtime import AgentRuntime
from agentmind.session.manager import SessionManager
from agentmind.session.types import Message
from agentmind.tools.base import Tool, ToolResult
from agentmind.tools.context import current_emit
from agentmind.tools.registry import ToolRegistry
from tests.conftest import make_settings
from tests.test_runner import MockProvider


def _stack(tmp_path, script, registry=None):
    settings = make_settings(tmp_path)
    provider = MockProvider(script)
    registry = registry or ToolRegistry()
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    sessions = SessionManager(settings.resolved_data_dir)
    runner = AgentRunner(provider, registry, memory, settings)
    return settings, memory, sessions, runner


async def _run_turn(bus, loop, session_id, text):
    outbound = []
    loop_task = asyncio.create_task(loop.run())
    try:
        await bus.publish(InboundMessage(session_id=session_id, text=text))
        while True:
            msg = await asyncio.wait_for(bus.receive_outbound(), timeout=5)
            outbound.append(msg)
            if msg.event in {"done", "error"}:
                break
    finally:
        loop.stop()
        await asyncio.wait_for(asyncio.shield(loop_task), timeout=5)
    return outbound


async def _wait_for(predicate, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_auto_voice_runs_after_answer(tmp_path):
    """Every plain answer triggers the auto-voice callback after "done"."""
    settings, memory, sessions, runner = _stack(tmp_path, ["最终答案"])
    bus = MessageBus()
    calls = []

    async def auto_voice(text, emit):
        calls.append(text)

    loop = AgentLoop(bus, runner, sessions, memory, settings, auto_voice=auto_voice)
    outbound = await _run_turn(bus, loop, "s1", "你好")
    assert outbound[-1].event == "done"
    # auto-voice is invoked after done, inside the same locked turn
    assert await _wait_for(lambda: bool(calls))
    assert calls == ["最终答案"]


class _FakeVoiceTool(Tool):
    """Stands in for the MCP voice tool: emits an audio attachment mid-turn."""

    name = "voice_speak"
    description = "fake tts"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, text: str = "", **kwargs) -> ToolResult:
        emit = current_emit()
        if emit is not None:
            await emit("attachment", {"mime": "audio/mpeg", "data": "", "label": ""})
        return ToolResult(output="已播报")


async def test_auto_voice_skipped_when_turn_already_spoke(tmp_path):
    """If the model called a voice tool itself, no second auto-voice bubble."""
    registry = ToolRegistry()
    registry.register(_FakeVoiceTool())
    settings, memory, sessions, runner = _stack(
        tmp_path,
        [{"id": "c1", "name": "voice_speak", "args": {"text": "hi"}}, "语音答案"],
        registry=registry,
    )
    bus = MessageBus()
    calls = []

    async def auto_voice(text, emit):
        calls.append(text)

    loop = AgentLoop(bus, runner, sessions, memory, settings, auto_voice=auto_voice)
    outbound = await _run_turn(bus, loop, "s2", "用语音回答我")
    assert outbound[-1].event == "done"
    assert any(m.event == "attachment" for m in outbound)
    # give a would-be auto-voice call a chance to fire — it must not
    await asyncio.sleep(0.2)
    assert calls == []


async def test_persist_turn_orders_user_before_voice_attachment(tmp_path):
    """A voice bubble persisted mid-turn must not end up before the question."""
    settings, memory, sessions, runner = _stack(tmp_path, [])
    loop = AgentLoop(bus=MessageBus(), runner=runner, sessions=sessions, memory=memory, settings=settings)
    session = sessions.create()
    # server fanout persists voice attachments while the turn is still running
    await sessions.append(
        session,
        Message(role="assistant", content="", attachment={"kind": "voice", "url": "/api/audio/x.mp3"}),
    )
    await loop._persist_turn(session, "问题", "回答")  # noqa: SLF001
    assert session.messages[0].role == "user" and session.messages[0].content == "问题"
    assert session.messages[1].attachment is not None
    assert session.messages[2].role == "assistant" and session.messages[2].content == "回答"


async def _server_stack(tmp_path):
    settings = make_settings(tmp_path, enable_web=False)
    runtime = AgentRuntime(settings)
    bus = MessageBus()
    loop = AgentLoop(bus, runtime.runner, runtime.sessions, runtime.memory, settings)
    server = AgentServer(runtime, bus, loop)
    await server.start("127.0.0.1", 0)
    port = server._runner.addresses[0][1]  # noqa: SLF001
    return settings, runtime, server, port


async def test_voice_upload_persists_user_message(tmp_path):
    settings, runtime, server, port = await _server_stack(tmp_path)
    try:
        session = runtime.sessions.create()
        async with aiohttp.ClientSession() as s:
            res = await s.post(
                f"http://127.0.0.1:{port}/api/sessions/{session.id}/voice",
                json={
                    "mime": "audio/webm;codecs=opus",
                    "data": base64.b64encode(b"WEBMDATA").decode(),
                },
            )
            assert res.status == 200
            body = await res.json()

            assert body["url"].endswith(".webm")
            path = settings.resolved_data_dir / "audio" / body["url"].rsplit("/", 1)[1]
            assert path.read_bytes() == b"WEBMDATA"

            msgs = runtime.sessions.get(session.id).messages
            assert msgs[-1].role == "user"
            assert msgs[-1].attachment["url"] == body["url"]
            assert body["id"] == msgs[-1].id

            # the audio is served back with an explicit audio/* content type
            r = await s.get(f"http://127.0.0.1:{port}{body['url']}")
            assert r.status == 200
            assert r.headers["Content-Type"] == "audio/webm"
    finally:
        await server.shutdown()
        await runtime.shutdown()


async def test_recall_message_window(tmp_path):
    settings, runtime, server, port = await _server_stack(tmp_path)
    try:
        session = runtime.sessions.create()
        await runtime.sessions.append(session, Message(role="user", content="撤回我"))
        recent_id = session.messages[-1].id
        old = Message(role="user", content="老消息", timestamp=time.time() - 300)
        await runtime.sessions.append(session, old)

        base = f"http://127.0.0.1:{port}/api/sessions/{session.id}/messages"
        async with aiohttp.ClientSession() as s:
            r = await s.delete(f"{base}/{recent_id}")
            assert r.status == 200
            # outside the 3-minute window
            r2 = await s.delete(f"{base}/{old.id}")
            assert r2.status == 403
            # unknown id
            r3 = await s.delete(f"{base}/nonexistent")
            assert r3.status == 404

        assert [m.content for m in session.messages] == ["老消息"]
        # the recall survives a reload from disk
        manager2 = SessionManager(settings.resolved_data_dir)
        assert [m.content for m in manager2.get(session.id).messages] == ["老消息"]
    finally:
        await server.shutdown()
        await runtime.shutdown()
