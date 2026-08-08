"""Tests for subagent delegation."""
from __future__ import annotations

from agentmind.core.runner import AgentRunner
from agentmind.core.subagent import SubagentManager
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.types import Session
from agentmind.tools.delegate_tool import DelegateTool
from agentmind.tools.registry import ToolRegistry
from tests.conftest import EchoTool, make_settings
from tests.test_runner import MockProvider


def _make_stack(tmp_path, script, max_depth=2):
    provider = MockProvider(script)
    registry = ToolRegistry()
    registry.register(EchoTool())
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    runner = AgentRunner(provider, registry, memory, make_settings(tmp_path))
    subagents = SubagentManager(runner, max_depth=max_depth)
    registry.register(DelegateTool(subagents))
    return provider, registry, runner, subagents


async def test_delegate_runs_isolated_subagent(tmp_path):
    provider, _, runner, _ = _make_stack(
        tmp_path,
        [{"id": "c", "name": "delegate", "args": {"task": "子任务"}}, "子代理完成", "父代理最终回答"],
    )
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    answer = await runner.run_turn(Session(id="s"), "委派一个子任务", emit)
    assert answer == "父代理最终回答"

    names = [e for e, _ in events]
    assert "subagent_start" in names and "subagent_end" in names

    # the subagent request used a dedicated system prompt and carried the task
    sub_request = provider.requests[1]
    assert sub_request[0]["role"] == "system"
    assert "子代理" in sub_request[0]["content"]
    assert sub_request[-1]["role"] == "user"
    assert sub_request[-1]["content"] == "子任务"


async def test_delegate_depth_guard(tmp_path):
    # depth 0 -> delegation is refused immediately without any model call
    _, _, _, subagents = _make_stack(tmp_path, [], max_depth=0)
    ok, result = await subagents.delegate("任意任务")
    assert not ok
    assert "上限" in result


async def test_nested_delegation_respects_depth(tmp_path):
    """parent -> subagent A -> subagent B -> delegate blocked at max depth."""
    script = [
        {"id": "1", "name": "delegate", "args": {"task": "A"}},  # parent
        {"id": "2", "name": "delegate", "args": {"task": "B"}},  # A (depth 1, allowed)
        {"id": "3", "name": "delegate", "args": {"task": "C"}},  # B (depth 2, blocked)
        "B 的结果",  # B answers after seeing the block
        "A 的结果",  # A answers
        "最终结果",  # parent answers
    ]
    provider, _, runner, _ = _make_stack(tmp_path, script, max_depth=2)

    async def emit(event, payload):
        pass

    answer = await runner.run_turn(Session(id="s"), "开始", emit)
    assert answer == "最终结果"

    # the blocked call happened at depth==max: verify a tool message mentioned the limit
    blocked = [
        m["content"]
        for req in provider.requests
        for m in req
        if m["role"] == "tool" and "上限" in m.get("content", "")
    ]
    assert blocked
