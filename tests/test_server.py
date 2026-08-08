"""End-to-end test: WebSocket -> bus -> ReAct loop -> tools, over a mock
OpenAI-compatible HTTP endpoint. Proves the full perception->react->act->answer
pipeline works with real HTTP/WebSocket transport."""

from __future__ import annotations

import json

import aiohttp
import pytest
from aiohttp import web

from agentmind.bus.queue import MessageBus
from agentmind.config import Settings
from agentmind.core.loop import AgentLoop
from agentmind.runtime import AgentRuntime


def _sse_chunk(delta: dict) -> bytes:
    return b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"


class MockOpenAIServer:
    """Streams a scripted conversation: tool call, then final answer."""

    def __init__(self) -> None:
        self._counter = 0
        self.app = web.Application()
        self.app.router.add_post("/v1/chat/completions", self._chat)

    async def _chat(self, request: web.Request) -> web.Response:
        body = await request.json()
        messages = body["messages"]
        self._counter += 1
        if self._counter == 1:
            args = json.dumps({}, ensure_ascii=False)
            chunks = [
                _sse_chunk({"role": "assistant", "tool_calls": [
                    {"index": 0, "id": "call-1", "type": "function",
                     "function": {"name": "get_current_time", "arguments": args}}
                ]}),
                b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"tool_calls\"}]}\n\n",
                b"data: [DONE]\n\n",
            ]
        else:
            tool_replies = [m for m in messages if m["role"] == "tool"]
            text = f"当前时间来自工具：{tool_replies[0]['content'].splitlines()[0]}" if tool_replies else "完成"
            chunks = [
                _sse_chunk({"role": "assistant", "content": ""}),
                *[_sse_chunk({"content": ch}) for ch in _split(text, 4)],
            ]
            chunks.append(b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n")
            chunks.append(b"data: [DONE]\n\n")

        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for chunk in chunks:
            await resp.write(chunk)
        await resp.write_eof()
        return resp


def _split(text: str, size: int):
    return [text[i : i + size] for i in range(0, len(text), size)]


@pytest.fixture
async def mock_openai():
    runner = web.AppRunner(MockOpenAIServer().app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    class Handle:
        def make_url(self, path: str) -> str:
            return f"http://127.0.0.1:{port}{path}"

        async def cleanup(self) -> None:
            await runner.cleanup()

    yield Handle()
    await runner.cleanup()


async def test_end_to_end_via_websocket(mock_openai, tmp_path):
    settings = Settings(
        data_dir=str(tmp_path / "data"),
        workspace=str(tmp_path / "workspace"),
        api_base=mock_openai.make_url("/v1"),
        api_key="",
        model="mock-model",
        memory_auto_store=False,
        enable_web=False,
    )

    runtime = AgentRuntime(settings)
    bus = MessageBus()
    loop = AgentLoop(bus, runtime.runner, runtime.sessions, runtime.memory, settings)

    from agentmind.api.server import AgentServer

    server = AgentServer(runtime, bus, loop)
    await server.start("127.0.0.1", 0)  # ephemeral port
    host, port = server._runner.addresses[0]  # noqa: SLF001
    try:
        loop_task = __import__("asyncio").create_task(loop.run())
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://{host}:{port}/ws") as ws:
                await ws.send_json({"type": "hello", "session_id": None})
                welcome = await ws.receive_json(timeout=10)
                assert welcome["event"] == "welcome"
                assert "tools" in welcome["payload"]
                assert "get_current_time" in welcome["payload"]["tools"]

                await ws.send_json({"type": "chat", "text": "现在几点了？"})

                events = []
                while True:
                    msg = await ws.receive_json(timeout=15)
                    events.append(msg["event"])
                    if msg["event"] == "done":
                        assert "当前时间" in msg["payload"]["answer"]
                        break

        assert "thinking_start" in events
        assert "tool_start" in events
        assert "tool_end" in events
        assert "delta" in events
        assert events[-1] == "done"
    finally:
        loop.stop()
        await __import__("asyncio").wait_for(__import__("asyncio").shield(loop_task), timeout=5)
        await server.shutdown()
        await runtime.shutdown()
