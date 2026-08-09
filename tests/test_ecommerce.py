"""Tests for the e-commerce after-sales domain (Phase 1)."""
from __future__ import annotations

from datetime import datetime

from agentmind.core.permissions import ApprovalGate, ApprovalManager, ApprovalPolicy
from agentmind.core.runner import AgentRunner
from agentmind.ecommerce.api import MockEcommerceAPI
from agentmind.ecommerce.rules import evaluate_after_sales
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.types import Session
from agentmind.tools.ecommerce import (
    AfterSalesApplyTool,
    AfterSalesCheckTool,
    LogisticsTrackTool,
    OrderLookupTool,
)
from agentmind.tools.registry import ToolRegistry
from tests.conftest import make_settings
from tests.test_runner import MockProvider

NOW = datetime(2026, 8, 9, 12, 0)


def make_api() -> MockEcommerceAPI:
    return MockEcommerceAPI(now=NOW)


# ---- mock API ----------------------------------------------------------
def test_lookup_order():
    api = make_api()
    order = api.lookup_order("JD20260801001")
    assert order and order["status"] == "已签收" and order["total"] == 249.0
    assert api.lookup_order("NOPE") is None


def test_track_logistics():
    api = make_api()
    events = api.track_logistics("JD20260801003")  # 运输中
    assert events and "运输中" in events[-1]["text"]
    assert api.track_logistics("NOPE") is None


# ---- rules engine ------------------------------------------------------
def test_rules_returnable_within_window():
    api = make_api()
    v = evaluate_after_sales(api, "JD20260801001")  # 签收 3 天
    assert v.allowed and not v.high_risk
    assert "七天无理由" in v.policy
    assert v.refund_amount == 249.0


def test_rules_expired():
    api = make_api()
    v = evaluate_after_sales(api, "JD20260801002")  # 签收 25 天
    assert not v.allowed and "超过" in v.reason


def test_rules_not_delivered():
    api = make_api()
    v = evaluate_after_sales(api, "JD20260801003")  # 运输中
    assert not v.allowed and "签收" in v.reason


def test_rules_duplicate_after_sales():
    api = make_api()
    v = evaluate_after_sales(api, "JD20260801005")  # 已有售后单
    assert not v.allowed and "重复" in v.reason


def test_rules_high_risk_amount():
    api = make_api()
    v = evaluate_after_sales(api, "JD20260801004")  # ¥8999
    assert v.allowed and v.high_risk


def test_rules_over_window_requires_quality_reason():
    api = make_api()
    # 假设签收 9 天：临时造一个订单
    api._orders["TEST09"] = {
        "order_id": "TEST09", "user": "测试", "status": "已签收",
        "items": [{"name": "测试商品", "qty": 1, "price": 100.0}], "total": 100.0,
        "created_at": "2026-07-30 10:00", "shipped_at": "2026-07-31 10:00",
        "delivered_at": "2026-07-31 18:00", "address": "x", "pay_method": "微信", "note": "",
    }
    v = evaluate_after_sales(api, "TEST09", reason="不想要了")
    assert not v.allowed  # 超期 + 非质量问题 → 拒绝
    v2 = evaluate_after_sales(api, "TEST09", reason="商品质量问题")
    assert v2.allowed


# ---- tools -------------------------------------------------------------
def _registry(api: MockEcommerceAPI) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(
        OrderLookupTool(api), LogisticsTrackTool(api),
        AfterSalesCheckTool(api), AfterSalesApplyTool(api),
    )
    return reg


async def test_apply_executes_refund():
    api = make_api()
    tool = AfterSalesApplyTool(api)
    result = await tool.run(order_id="JD20260801001", reason="七天无理由退货")
    assert result.output.startswith("✅ 售后申请已提交")
    assert "退单号" in result.output
    assert api.order_after_sales_records("JD20260801001")  # record persisted
    assert api.lookup_order("JD20260801001")["status"] == "售后处理中"


async def test_apply_rejects_ineligible():
    api = make_api()
    tool = AfterSalesApplyTool(api)
    result = await tool.run(order_id="JD20260801002", reason="七天无理由退货")
    assert result.is_error and "不符合" in result.output


# ---- approval integration ----------------------------------------------
def test_policy_marks_after_sales_apply_risky():
    policy = ApprovalPolicy("ask_risky", extra_risky={"after_sales_apply"})
    assert policy.requires("after_sales_apply")
    assert not policy.requires("after_sales_check")


async def test_runner_approves_after_sales_apply(tmp_path):
    """Scripted model calls after_sales_apply -> approval modal -> executes."""
    api = make_api()
    manager = ApprovalManager(timeout=5)
    gate = ApprovalGate(ApprovalPolicy("ask_risky", extra_risky={"after_sales_apply"}), manager)
    provider = MockProvider([
        {"id": "c1", "name": "after_sales_apply", "args": {"order_id": "JD20260801001", "reason": "七天无理由退货"}},
        "好的，已为您提交退货申请。",
    ])
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    runner = AgentRunner(provider, _registry(api), memory, make_settings(tmp_path), approval=gate)

    events = []

    async def emit(event, payload):
        events.append((event, payload))
        if event == "approval_request":
            manager.respond(payload["approval_id"], True)

    answer = await runner.run_turn(Session(id="s"), "我要退货", emit)
    assert answer == "好的，已为您提交退货申请。"
    names = [e for e, _ in events]
    assert "approval_request" in names and "approval_result" in names
    # the refund was actually executed -> tool observation fed back to the model
    tool_msgs = [m["content"] for req in provider.requests for m in req if m["role"] == "tool"]
    assert tool_msgs and "退单号" in tool_msgs[0]
    assert api.order_after_sales_records("JD20260801001")
