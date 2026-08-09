"""Tests for e-commerce Phase 2: policy KB, service state machine, escalation."""
from __future__ import annotations

from datetime import datetime

from agentmind.ecommerce.policies import search_policies
from agentmind.ecommerce.service_state import ServiceSessionTracker
from agentmind.session.manager import SessionManager
from agentmind.tools.context import request_context
from agentmind.tools.ecommerce import (
    AfterSalesPolicyTool,
    EscalateHumanTool,
    ResolveIssueTool,
)
from tests.conftest import make_settings


# ---- policy knowledge base ----------------------------------------------
def test_policy_retrieval():
    hits = search_policies("退货运费谁出？")
    assert hits and any("运费" in p["topic"] for p in hits)

    hits2 = search_policies("退款多久到账")
    assert hits2 and any("退款" in p["topic"] for p in hits2)

    assert search_policies("完全不相关的话题xyzq") == []


async def test_policy_tool():
    tool = AfterSalesPolicyTool()
    result = await tool.run(query="运费险赔多少")
    assert "售后政策" in result.output
    assert "运费" in result.output


# ---- state machine ------------------------------------------------------
def _manager(tmp_path) -> SessionManager:
    return SessionManager(make_settings(tmp_path).resolved_data_dir)


async def test_note_activity_moves_to_processing(tmp_path):
    manager = _manager(tmp_path)
    tracker = ServiceSessionTracker(manager)
    session = manager.create()
    state = await tracker.note_activity(session.id)
    assert state == "processing"
    assert session.service_state == "processing"


async def test_escalate_creates_ticket(tmp_path):
    manager = _manager(tmp_path)
    tracker = ServiceSessionTracker(manager)
    session = manager.create()
    result = await tracker.escalate(session.id, "用户情绪激动", now=datetime(2026, 8, 9, 12, 0))
    assert result["ticket_id"].startswith("TS")
    assert session.service_state == "escalated"
    assert "情绪" in result["reason"]


async def test_resolve(tmp_path):
    manager = _manager(tmp_path)
    tracker = ServiceSessionTracker(manager)
    session = manager.create()
    await tracker.resolve(session.id)
    assert session.service_state == "resolved"


async def test_terminal_states_do_not_regress(tmp_path):
    manager = _manager(tmp_path)
    tracker = ServiceSessionTracker(manager)
    session = manager.create()
    await tracker.resolve(session.id)
    # activity on a resolved session must not move it back to processing
    await tracker.note_activity(session.id)
    assert session.service_state == "resolved"


async def test_timeout_escalates_stale_sessions(tmp_path):
    manager = _manager(tmp_path)
    tracker = ServiceSessionTracker(manager)
    session = manager.create()
    await tracker.note_activity(session.id)
    session.updated_at = session.updated_at - 3600  # 1 hour ago, timeout=5min
    escalations = await tracker.check_timeout(timeout_minutes=5)
    assert len(escalations) == 1
    assert escalations[0]["session_id"] == session.id
    assert session.service_state == "escalated"


# ---- escalation tools ---------------------------------------------------
async def test_escalate_human_tool(tmp_path):
    manager = _manager(tmp_path)
    session = manager.create()
    tool = EscalateHumanTool(ServiceSessionTracker(manager))
    events = []

    async def emit(event, payload):
        events.append((event, payload))

    async with request_context(emit, session_id=session.id):
        result = await tool.run(reason="用户要投诉")

    assert result.output.startswith("✅ 已转接人工客服")
    assert "工单号" in result.output
    assert any(e == "service_state" for e, _ in events)
    assert session.service_state == "escalated"


async def test_resolve_issue_tool(tmp_path):
    manager = _manager(tmp_path)
    session = manager.create()
    tool = ResolveIssueTool(ServiceSessionTracker(manager))
    async with request_context(None, session_id=session.id):
        result = await tool.run()
    assert "已解决" in result.output
    assert session.service_state == "resolved"


async def test_escalate_without_session_returns_error(tmp_path):
    tracker = ServiceSessionTracker(_manager(tmp_path))
    result = await tracker.escalate("nope", "x")
    assert "error" in result
