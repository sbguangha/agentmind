"""E-commerce customer-service tools (Phase 1: order / logistics / after-sales).

These tools talk only to the mock open-platform API; the rules engine decides
after-sales eligibility. ``after_sales_apply`` mutates real state (a refund),
so it is registered as an approval-required tool in the runtime.
"""
from __future__ import annotations

from agentmind.ecommerce.api import MockEcommerceAPI
from agentmind.ecommerce.rules import evaluate_after_sales
from agentmind.tools.base import Tool, ToolResult


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

    def __init__(self, api: MockEcommerceAPI) -> None:
        self._api = api

    async def run(self, order_id: str, reason: str = "", **kwargs) -> ToolResult:
        verdict = evaluate_after_sales(self._api, order_id.strip(), reason or "")
        return ToolResult(output=verdict.to_text(order_id.strip()))


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

    def __init__(self, api: MockEcommerceAPI) -> None:
        self._api = api

    async def run(self, order_id: str, reason: str, **kwargs) -> ToolResult:
        order_id = order_id.strip()
        verdict = evaluate_after_sales(self._api, order_id, reason or "")
        if not verdict.allowed:
            return ToolResult(output=verdict.to_text(order_id), is_error=True)

        record = self._api.create_after_sales(order_id, reason or "", verdict.refund_amount, verdict.policy)
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
