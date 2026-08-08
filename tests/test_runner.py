"""Tests for the ReAct runner: reason -> act -> observe -> answer."""
from __future__ import annotations

import json

import pytest

from agentmind.config import Settings
from agentmind.core.runner import AgentRunner
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.providers.base import ChatResult, StreamChunk, ToolCall, ToolCallDelta
from agentmind.session.types import Session
from agentmind.tools.registry import ToolRegistry
from tests.conftest import EchoTool


class MockProvider:
    """Scripted provider: each entry is either an answer string or a list of
    tool-call specs {'id','name','args'}."""

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[list[dict]] = []

    async def stream(self, messages, model, *, tools=None, temperature=0.7):
        self.requests.append(messages)
        item = self._script.pop(0)
        if isinstance(item, str):
            for ch in _chunks(item, size=3):
                yield StreamChunk(content_delta=ch)
            yield StreamChunk(finish_reason="stop")
        else:
            specs = item if isinstance(item, list) else [item]
            for spec in specs:
                yield StreamChunk(
                    tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id=spec["id"],
                            name=spec["name"],
                            arguments=json.dumps(spec["args"], ensure_ascii=False),
                        )
                    ]
                )
            yield StreamChunk(finish_reason="tool_calls")

    async def complete(self, messages, model, *, tools=None, temperature=0.7):
        self.requests.append(messages)
        item = self._script.pop(0)
        if isinstance(item, str):
            return ChatResult(content=item, finish_reason="stop")
        specs = item if isinstance(item, list) else [item]
        calls = [
            ToolCall(id=sp["id"], name=sp["name"], arguments=json.dumps(sp["args"], ensure_ascii=False))
            for sp in specs
        ]
        return ChatResult(tool_calls=calls, finish_reason="tool_calls")

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]


def _chunks(text: str, size: int = 2):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def make_runner(script, settings: Settings, tmp_path, registry: ToolRegistry):
    provider = MockProvider(script)
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    return AgentRunner(provider, registry, memory, settings), provider


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


async def test_react_loop_executes_tool_then_answers(settings, tmp_path):
    runner, provider = make_runner(
        [{"id": "c1", "name": "echo", "args": {"text": "hello"}}, "最终答案"],
        settings,
        tmp_path,
        make_registry(),
    )
    session = Session(id="s1")
    events: list[tuple[str, dict]] = []

    async def emit(event: str, payload: dict) -> None:
        events.append((event, payload))

    answer = await runner.run_turn(session, "请回声 hello", emit)

    assert answer == "最终答案"
    event_names = [e for e, _ in events]
    assert "tool_start" in event_names and "tool_end" in event_names
    assert "delta" in event_names  # final answer was streamed

    # the tool observation was fed back into the second request
    second = provider.requests[1]
    tool_replies = [m for m in second if m["role"] == "tool"]
    assert tool_replies and tool_replies[0]["content"] == "echo:hello"
    # assistant tool_call message present in the second request
    assistant_msgs = [m for m in second if m["role"] == "assistant" and m.get("tool_calls")]
    assert assistant_msgs and assistant_msgs[0]["tool_calls"][0]["function"]["name"] == "echo"


async def test_react_loop_direct_answer_without_tools(settings, tmp_path):
    runner, provider = make_runner(["直接回答", "另一个"], settings, tmp_path, make_registry())
    session = Session(id="s2")
    events: list[tuple[str, dict]] = []

    async def emit(event: str, payload: dict) -> None:
        events.append((event, payload))

    answer = await runner.run_turn(session, "你好", emit)
    assert answer == "直接回答"
    assert len(provider.requests) == 1  # no second round


async def test_react_loop_guards_against_infinite_tool_rounds(settings, tmp_path):
    never_stop = [{"id": "c", "name": "echo", "args": {"text": "loop"}}] * 10
    runner, _ = make_runner(never_stop, settings, tmp_path, make_registry())
    session = Session(id="s3")

    async def emit(event: str, payload: dict) -> None:
        pass

    with pytest.raises(RuntimeError, match="最大工具轮数"):
        await runner.run_turn(session, "loop", emit)


async def test_system_prompt_injects_perception(settings, tmp_path):
    """The first request must carry system prompt + the user message."""
    runner, provider = make_runner(["回答"], settings, tmp_path, make_registry())
    session = Session(id="s4")

    async def emit(event: str, payload: dict) -> None:
        pass

    await runner.run_turn(session, "测试提问", emit)
    first = provider.requests[0]
    assert first[0]["role"] == "system"
    assert "当前时间" in first[0]["content"]
    assert "AgentMind" in first[0]["content"]
    assert first[-1] == {"role": "user", "content": "测试提问"}


class _CaptureTools(MockProvider):
    def __init__(self, script):
        super().__init__(script)
        self._last_tools = None

    async def stream(self, messages, model, *, tools=None, temperature=0.7):
        self._last_tools = tools or []
        async for chunk in super().stream(messages, model, tools=tools, temperature=temperature):
            yield chunk


async def test_system_prompt_tools_schema(settings, tmp_path):
    provider = _CaptureTools(["回答"])
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    runner = AgentRunner(provider, make_registry(), memory, settings)

    async def emit(event: str, payload: dict) -> None:
        pass

    await runner.run_turn(Session(id="s5"), "hi", emit)
    names = [t["function"]["name"] for t in provider._last_tools]
    assert "echo" in names
