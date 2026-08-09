"""Phase 3 tools: user profile, preferences, price comparison, QA/satisfaction.

These extend the after-sales agent with differentiation (profile-driven
service), delegation (subagent price comparison) and self-QA (satisfaction
estimation before replying).
"""
from __future__ import annotations

from agentmind.config import Settings
from agentmind.core.subagent import SubagentManager
from agentmind.ecommerce.profile import UserProfileTracker
from agentmind.providers.base import LLMProvider
from agentmind.tools.base import Tool, ToolResult
from agentmind.tools.context import current_emit


class UserProfileTool(Tool):
    name = "user_profile"
    description = (
        "查询用户画像（会员等级、订单数、消费总额、退货频率、偏好）。"
        "服务用户前可先查看，以便提供差异化服务（如金卡会员优先处理）。"
    )

    parameters = {
        "type": "object",
        "properties": {"user": {"type": "string", "description": "用户名（来自订单中的 user 字段）"}},
        "required": ["user"],
    }

    def __init__(self, tracker: UserProfileTracker) -> None:
        self._tracker = tracker

    async def run(self, user: str, **kwargs) -> ToolResult:
        return ToolResult(output=self._tracker.format(self._tracker.profile(user.strip())))


class RememberPreferenceTool(Tool):
    name = "remember_preference"
    description = "记住该用户的偏好，用于以后的服务（如：偏好简洁回复、喜欢电话联系、退款要加快）。"

    parameters = {
        "type": "object",
        "properties": {
            "user": {"type": "string", "description": "用户名"},
            "note": {"type": "string", "description": "要记住的偏好内容"},
        },
        "required": ["user", "note"],
    }

    def __init__(self, tracker: UserProfileTracker) -> None:
        self._tracker = tracker

    async def run(self, user: str, note: str, **kwargs) -> ToolResult:
        self._tracker.remember(user.strip(), note.strip())
        return ToolResult(output=f"已记住 {user.strip()} 的偏好：{note.strip()}")


class PriceCompareTool(Tool):
    name = "price_compare"
    description = (
        "用子代理搜索并对比某商品在各电商平台（京东/淘宝/拼多多等）的价格。"
        "用户问'贵不贵/哪里买更便宜/比价' 时调用。需要联网。"
    )

    parameters = {
        "type": "object",
        "properties": {"item": {"type": "string", "description": "商品名称（尽量带上品牌/型号）"}},
        "required": ["item"],
    }

    def __init__(self, subagents: SubagentManager) -> None:
        self._subagents = subagents

    async def run(self, item: str, **kwargs) -> ToolResult:
        task = (
            f"搜索「{item}」在京东、淘宝/天猫、拼多多等电商平台的当前售价，"
            "对比各平台价格，返回简洁的比价结论（每条含平台、价格、约 X 元）。"
            "如果部分平台搜不到，如实说明。"
        )
        emit = current_emit()
        if emit is not None:
            await emit("subagent_start", {"task": task})
        ok, result = await self._subagents.delegate(task)
        if emit is not None:
            await emit("subagent_end", {"task": task, "result": result, "success": ok})
        if not ok:
            return ToolResult(output=f"比价失败：{result}", is_error=True)
        return ToolResult(output=result)


class ServiceQualityCheckTool(Tool):
    name = "service_quality_check"
    description = (
        "发送回复前，对自己的客服话术做质检：评估回复的共情力、清晰度、问题解决程度，"
        "并给出满意度预估（1-5 分）与改进建议。用于重要或复杂的售后场景。"
    )

    parameters = {
        "type": "object",
        "properties": {"reply": {"type": "string", "description": "你准备回复用户的话术"}},
        "required": ["reply"],
    }

    def __init__(self, provider: LLMProvider, settings: Settings) -> None:
        self._provider = provider
        self._settings = settings

    async def run(self, reply: str, **kwargs) -> ToolResult:
        prompt = (
            "你是电商客服质检员。评估下面这条客服回复，从三个维度各打 1-5 分：共情力、清晰度、问题解决程度；"
            "再给出综合满意度预估（1-5 分）和一句改进建议。\n"
            "输出格式：\n共情:X\n清晰:X\n解决:X\n满意度:X\n建议:..."
        )
        try:
            result = await self._provider.complete(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"客服回复：\n{reply}"},
                ],
                self._settings.model,
                temperature=0.3,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"质检失败: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(output=f"📋 话术质检\n{result.content or '（无输出）'}")
