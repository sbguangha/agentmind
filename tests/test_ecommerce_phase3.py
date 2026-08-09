"""Tests for e-commerce Phase 3: user profile, preferences, price compare, QA."""
from __future__ import annotations

from datetime import datetime

from agentmind.core.runner import AgentRunner
from agentmind.core.subagent import SubagentManager
from agentmind.ecommerce.api import MockEcommerceAPI
from agentmind.ecommerce.profile import UserProfileTracker, profile_hint
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.types import Session
from agentmind.tools.ecommerce import AfterSalesCheckTool
from agentmind.tools.ecommerce_analytics import (
    PriceCompareTool,
    RememberPreferenceTool,
    ServiceQualityCheckTool,
    UserProfileTool,
)
from agentmind.tools.registry import ToolRegistry
from tests.conftest import EchoTool, make_settings
from tests.test_runner import MockProvider

NOW = datetime(2026, 8, 9, 12, 0)


def make_api() -> MockEcommerceAPI:
    return MockEcommerceAPI(now=NOW)


# ---- profile -----------------------------------------------------------
def test_profile_member_level():
    api = make_api()
    p = UserProfileTracker(api).profile("张三")  # 手环249 + 手机2999 + mac8999 ≈ 12247
    assert p["member_level"] == "金卡"
    assert p["preferences"]  # seeded preferences exist

    p2 = UserProfileTracker(api).profile("李四")  # 鼠标499
    assert p2["member_level"] == "普通"


def test_profile_hint():
    api = make_api()
    hint = profile_hint(api, "张三")
    assert "金卡" in hint


async def test_profile_tool():
    api = make_api()
    tool = UserProfileTool(UserProfileTracker(api))
    result = await tool.run(user="张三")
    assert "金卡" in result.output


async def test_remember_preference_tool():
    api = make_api()
    tracker = UserProfileTracker(api)
    tool = RememberPreferenceTool(tracker)
    result = await tool.run(user="张三", note="退款要加快处理")
    assert "已记住" in result.output
    assert "退款要加快处理" in tracker.profile("张三")["preferences"]


async def test_check_attaches_profile_hint():
    api = make_api()
    tool = AfterSalesCheckTool(api)
    result = await tool.run(order_id="JD20260801001")  # 张三，金卡
    assert "金卡" in result.output


# ---- price compare (subagent) ------------------------------------------
async def test_price_compare_delegates(tmp_path):
    provider = MockProvider([
        {"id": "p1", "name": "price_compare", "args": {"item": "罗技 MX Master 3S"}},
        "子代理比价完成",
        "最终：推荐在京东购买。",
    ])
    registry = ToolRegistry()
    registry.register(EchoTool())
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    runner = AgentRunner(provider, registry, memory, make_settings(tmp_path))
    subagents = SubagentManager(runner, max_depth=2)
    registry.register(PriceCompareTool(subagents))

    events = []

    async def emit(event, payload):
        events.append((event, payload))

    answer = await runner.run_turn(Session(id="s"), "这个鼠标贵不贵", emit)
    assert answer == "最终：推荐在京东购买。"
    names = [e for e, _ in events]
    assert "subagent_start" in names and "subagent_end" in names


# ---- service quality check ---------------------------------------------
async def test_service_quality_check(tmp_path):
    provider = MockProvider(["共情:4\n清晰:5\n解决:4\n满意度:4\n建议:给出明确退单号"])
    tool = ServiceQualityCheckTool(provider, make_settings(tmp_path))
    result = await tool.run(reply="您的退单号是 AS123，感谢理解。")
    assert "满意度:4" in result.output
    assert not result.is_error
