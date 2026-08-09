"""E-commerce customer-service tools (Phase 1 + 2).

Phase 1: order / logistics / after-sales eligibility + refund execution.
Phase 2: policy knowledge base (RAG), customer-service state machine
(escalate_human / resolve_issue), timeout escalation.

These tools talk only to the mock open-platform API and the rules engine; the
after-sales state machine drives the session lifecycle.
"""
from __future__ import annotations

from agentmind.ecommerce.api import MockEcommerceAPI
from agentmind.ecommerce.policies import search_policies
from agentmind.ecommerce.rules import evaluate_after_sales
from agentmind.ecommerce.service_state import ServiceSessionTracker
from agentmind.tools.base import Tool, ToolResult
from agentmind.tools.context import current_emit, current_session_id


class OrderLookupTool(Tool):
    name = "order_lookup"
    description = (
        "根据订单号查询订单详情（商品、金额、状态、收货地址）。"
        "用户询问订单/买了什么/发货没有 时调用。"
    )

    parameters = {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "订单号，形如 JD20260801001"}},
        "required": ["order_id"],
    }

    def __init__(self, api: MockEcommerceAPI) -> None:
        self._api = api

    async def run(self, order_id: str, **kwargs) -> ToolResult:
        order = self._api.lookup_order(order_id.strip())
        if order is None:
            return ToolResult(output=f"未找到订单 {order_id}，请核对订单号。", is_error=True)
        items = "\n".join(
            f"  - {i['name']} × {i['qty']}  ¥{i['price']:,.2f}" for i in order["items"]
        )
        return ToolResult(
            output=(
                f"订单 {order['order_id']}（{order['user']}）\n"
                f"状态：{order['status']}\n"
                f"商品：\n{items}\n"
                f"合计：¥{order['total']:,.2f}（{order['pay_method']}）\n"
                f"下单时间：{order['created_at']}\n"
                f"收货地址：{order['address']}"
            ),
            data={"order": order},
        )


class LogisticsTrackTool(Tool):
    name = "logistics_track"
    description = "查询订单的物流轨迹（最近更新）。用户问快递到哪了/什么时候到 时调用。"

    parameters = {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "订单号"}},
        "required": ["order_id"],
    }

    def __init__(self, api: MockEcommerceAPI) -> None:
        self._api = api

    async def run(self, order_id: str, **kwargs) -> ToolResult:
        order = self._api.lookup_order(order_id.strip())
        if order is None:
            return ToolResult(output=f"未找到订单 {order_id}。", is_error=True)
        events = self._api.track_logistics(order_id.strip())
        if not events:
            return ToolResult(output=f"订单 {order_id} 尚未发货，暂无物流信息。")
        lines = [f"订单 {order_id} 物流轨迹（{order['status']}）："]
        for event in events:
            lines.append(f"  [{event['time']}] {event['text']}")
        return ToolResult(output="\n".join(lines))


class AfterSalesCheckTool(Tool):
    name = "after_sales_check"
    description = (
        "校验订单是否符合退货/退款条件（只读，安全）。申请售后前必须先调用本工具，"
        "以规则引擎的判断为准，不要自行猜测售后政策。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
            "reason": {"type": "string", "description": "售后原因（如：七天无理由退货/商品质量问题），可选"},
        },
        "required": ["order_id"],
    }

    def __init__(self, api: MockEcommerceAPI, tracker: ServiceSessionTracker | None = None) -> None:
        self._api = api
        self._tracker = tracker

    async def run(self, order_id: str, reason: str = "", **kwargs) -> ToolResult:
        if self._tracker is not None and current_session_id():
            changed = await self._tracker.note_activity(current_session_id())
            if changed:
                await self._emit_service_state("processing", "售后处理中")
        order_id = order_id.strip()
        verdict = evaluate_after_sales(self._api, order_id, reason or "")
        output = verdict.to_text(order_id)
        order = self._api.lookup_order(order_id)
        if verdict.allowed and order is not None:
            from agentmind.ecommerce.profile import profile_hint

            output += profile_hint(self._api, order["user"])
        return ToolResult(output=output)

    async def _emit_service_state(self, state: str, label: str, note: str = "") -> None:
        emit = current_emit()
        if emit is not None:
            await emit("service_state", {"state": state, "label": label, "note": note})


class AfterSalesApplyTool(Tool):
    name = "after_sales_apply"
    description = (
        "提交退货/退款申请并执行（高风险操作，会请求用户人工审批；审批通过才执行退款）。"
        "必须先通过 after_sales_check 确认符合条件。请在主对话中调用，勿委派给子代理。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
            "reason": {"type": "string", "description": "售后原因（如：七天无理由退货/商品质量问题）"},
        },
        "required": ["order_id", "reason"],
    }

    def __init__(self, api: MockEcommerceAPI, tracker: ServiceSessionTracker | None = None) -> None:
        self._api = api
        self._tracker = tracker

    async def run(self, order_id: str, reason: str, **kwargs) -> ToolResult:
        order_id = order_id.strip()
        verdict = evaluate_after_sales(self._api, order_id, reason or "")
        if not verdict.allowed:
            return ToolResult(output=verdict.to_text(order_id), is_error=True)

        record = self._api.create_after_sales(order_id, reason or "", verdict.refund_amount, verdict.policy)
        if self._tracker is not None and current_session_id():
            changed = await self._tracker.note_activity(current_session_id())
            if changed:
                await self._emit_service_state("processing", "售后处理中")
        return ToolResult(
            output=(
                f"✅ 售后申请已提交\n"
                f"  退单号：{record['after_sales_id']}\n"
                f"  订单：{order_id}\n"
                f"  原因：{record['reason']}\n"
                f"  退款金额：¥{record['refund_amount']:,.2f}\n"
                f"  状态：{record['status']}"
            ),
            data={"after_sales": record},
        )


class AfterSalesPolicyTool(Tool):
    name = "after_sales_policy"
    description = (
        "查询平台售后政策（退货运费谁出、退款多久到账、运费险、哪些商品不支持退货、"
        "售后流程等）。用户问政策/规则/多久到账/能不能退 时调用，按政策如实回答。"
    )

    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "要查询的政策主题，如：退货运费谁出"}},
        "required": ["query"],
    }

    async def run(self, query: str, **kwargs) -> ToolResult:
        hits = search_policies(query)
        if not hits:
            return ToolResult(output="没有找到相关售后政策。")
        lines = ["📋 相关售后政策："]
        for policy in hits:
            lines.append(f"· {policy['topic']}：{policy['text']}")
        return ToolResult(output="\n".join(lines))


class EscalateHumanTool(Tool):
    name = "escalate_human"
    description = (
        "把当前售后会话转给人工客服，生成工单。当用户情绪激动、问题超出处理范围、"
        "或用户明确要求转人工时调用。请勿委派给子代理。"
    )

    parameters = {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "转人工的原因"}},
        "required": ["reason"],
    }

    def __init__(self, tracker: ServiceSessionTracker) -> None:
        self._tracker = tracker

    async def run(self, reason: str, **kwargs) -> ToolResult:
        session_id = current_session_id()
        result = await self._tracker.escalate(session_id or "", reason)
        if result.get("error"):
            return ToolResult(output=result["error"], is_error=True)
        emit = current_emit()
        if emit is not None:
            await emit("service_state", {"state": "escalated", "label": "已转人工", "note": f"工单号 {result['ticket_id']}"})
        return ToolResult(
            output=(
                f"✅ 已转接人工客服\n"
                f"  工单号：{result['ticket_id']}\n"
                f"  原因：{result['reason']}\n"
                f"  时间：{result['created_at']}\n"
                f"  请稍候，人工客服将尽快接入。"
            )
        )


class ResolveIssueTool(Tool):
    name = "resolve_issue"
    description = "标记当前售后问题已解决（如用户确认问题处理完毕）。"

    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, tracker: ServiceSessionTracker) -> None:
        self._tracker = tracker

    async def run(self, **kwargs) -> ToolResult:
        session_id = current_session_id()
        result = await self._tracker.resolve(session_id or "")
        if result.get("error"):
            return ToolResult(output=result["error"], is_error=True)
        emit = current_emit()
        if emit is not None:
            await emit("service_state", {"state": "resolved", "label": "已解决", "note": ""})
        return ToolResult(output=f"✅ 本次售后问题已标记为已解决（{result['resolved_at']}）。感谢您的耐心！")
