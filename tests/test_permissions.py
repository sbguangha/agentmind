"""Tests for human-in-the-loop tool approval."""
from __future__ import annotations

import asyncio

from agentmind.core.permissions import ApprovalGate, ApprovalManager, ApprovalPolicy
from agentmind.core.runner import AgentRunner
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.types import Session
from agentmind.tools.registry import ToolRegistry
from tests.conftest import EchoTool, make_settings
from tests.test_runner import MockProvider


# ---- policy ----------------------------------------------------------
def test_policy_modes():
    auto = ApprovalPolicy("auto")
    risky = ApprovalPolicy("ask_risky")
    all_ = ApprovalPolicy("ask_all")
    assert not auto.requires("write_file")
    assert not auto.requires("web_search")
    assert risky.requires("write_file")
    assert risky.requires("run_shell")
    assert not risky.requires("web_search")
    assert not risky.requires("echo")
    assert all_.requires("web_search")
    assert all_.requires("echo")


# ---- manager ---------------------------------------------------------
async def test_manager_approve():
    manager = ApprovalManager(timeout=5)
    approval_id = manager.new_request("write_file", "{}")

    async def respond():
        await asyncio.sleep(0.02)
        manager.respond(approval_id, True)

    task = asyncio.create_task(respond())
    assert await manager.wait(approval_id) is True
    await task


async def test_manager_deny():
    manager = ApprovalManager(timeout=5)
    approval_id = manager.new_request("write_file", "{}")
    assert manager.respond(approval_id, False) is True
    assert await manager.wait(approval_id) is False


async def test_manager_timeout_rejects():
    manager = ApprovalManager(timeout=0.15)
    approval_id = manager.new_request("write_file", "{}")
    assert await manager.wait(approval_id) is False


# ---- runner integration ----------------------------------------------
def _make_runner(script, tmp_path, gate):
    provider = MockProvider(script)
    registry = ToolRegistry()
    registry.register(EchoTool())
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    return AgentRunner(provider, registry, memory, make_settings(tmp_path), approval=gate), provider


async def test_runner_approves_and_executes(tmp_path):
    manager = ApprovalManager(timeout=5)
    gate = ApprovalGate(ApprovalPolicy("ask_all"), manager)
    runner, _ = _make_runner([{"id": "c", "name": "echo", "args": {"text": "hi"}}, "完成"], tmp_path, gate)

    events = []

    async def emit(event, payload):
        events.append((event, payload))
        if event == "approval_request":
            manager.respond(payload["approval_id"], True)

    answer = await runner.run_turn(Session(id="s"), "hi", emit)
    assert answer == "完成"
    names = [e for e, _ in events]
    assert "approval_request" in names and "approval_result" in names
    assert "tool_end" in names
    # echo was executed -> observation fed back
    assert any("echo:hi" in m["content"] for req in _reqs(runner) for m in req if m["role"] == "tool")


async def test_runner_rejects_and_tells_model(tmp_path):
    manager = ApprovalManager(timeout=5)
    gate = ApprovalGate(ApprovalPolicy("ask_all"), manager)
    runner, _ = _make_runner([{"id": "c", "name": "echo", "args": {"text": "hi"}}, "知道了"], tmp_path, gate)

    events = []

    async def emit(event, payload):
        events.append((event, payload))
        if event == "approval_request":
            manager.respond(payload["approval_id"], False)

    answer = await runner.run_turn(Session(id="s"), "hi", emit)
    assert answer == "知道了"
    # the tool observation told the model it was rejected
    tool_msgs = [
        m for req in _reqs(runner) for m in req if m["role"] == "tool"
    ]
    assert tool_msgs and "拒绝" in tool_msgs[0]["content"]


async def test_auto_gate_never_blocks(tmp_path):
    runner, _ = _make_runner([{"id": "c", "name": "echo", "args": {"text": "hi"}}, "完成"], tmp_path, ApprovalGate.auto())
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    answer = await runner.run_turn(Session(id="s"), "hi", emit)
    assert answer == "完成"
    assert not any(e == "approval_request" for e, _ in events)


def _reqs(runner):
    provider = runner._provider
    return provider.requests
