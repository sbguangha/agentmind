"""After-sales rules engine.

The engine is the source of truth for *eligibility* — the LLM translates the
user's intent, the engine decides policy. This separation is the engineering
hallmark of a production e-commerce agent: refunds are never model-judged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentmind.ecommerce.api import MockEcommerceAPI

RETURN_WINDOW_DAYS = 7  # seven-day no-reason return
MAX_RETURN_DAYS = 15  # absolute return deadline
HIGH_RISK_AMOUNT = 1000.0  # refunds above this trigger human approval


@dataclass
class AfterSalesVerdict:
    allowed: bool
    reason: str
    policy: str
    refund_amount: float
    high_risk: bool

    def to_text(self, order_id: str) -> str:
        if not self.allowed:
            return f"❌ 订单 {order_id} 不符合退货条件：{self.reason}"
        lines = [
            f"✅ 订单 {order_id} 符合退货条件",
            f"  适用政策：{self.policy}",
            f"  可退金额：¥{self.refund_amount:,.2f}",
        ]
        if self.high_risk:
            lines.append("  ⚠️ 退款金额较高，提交申请需要用户人工审批")
        return "\n".join(lines)


def _days_since(dt_str: str, now: datetime) -> float | None:
    if not dt_str:
        return None
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return (now - dt).total_seconds() / 86400


def evaluate_after_sales(
    api: MockEcommerceAPI,
    order_id: str,
    reason: str = "",
    *,
    now: datetime | None = None,
) -> AfterSalesVerdict:
    """Evaluate whether *order_id* can be returned/refunded."""
    now = now or api._now
    order = api.lookup_order(order_id)
    if order is None:
        return AfterSalesVerdict(False, f"未找到订单 {order_id}，请核对订单号。", "", 0.0, False)
    if api.order_after_sales_records(order_id):
        return AfterSalesVerdict(False, "该订单已存在进行中的售后申请，请勿重复提交。", "", 0.0, False)
    if order["status"] != "已签收":
        return AfterSalesVerdict(
            False, f"订单当前状态为「{order['status']}」，需签收后才能申请售后。", "", 0.0, False
        )

    days = _days_since(order["delivered_at"], now)
    if days is None:
        return AfterSalesVerdict(False, "无法确定签收时间。", "", 0.0, False)

    if days > MAX_RETURN_DAYS:
        return AfterSalesVerdict(
            False,
            f"已签收 {days:.0f} 天，超过最长退货期限（{MAX_RETURN_DAYS} 天），无法退货。",
            "", 0.0, False,
        )

    if days <= RETURN_WINDOW_DAYS:
        policy = f"七天无理由退货（签收 {days:.0f} 天，在 {RETURN_WINDOW_DAYS} 天有效期内）"
    else:
        policy = f"超期售后（签收 {days:.0f} 天，仅支持商品质量问题）"
        if "质量" not in (reason or ""):
            return AfterSalesVerdict(
                False,
                f"已签收 {days:.0f} 天，超过 7 天无理由期；如需售后请选择「商品质量问题」原因。",
                policy, 0.0, False,
            )

    refund = order["total"]
    return AfterSalesVerdict(
        allowed=True, reason="符合退货条件", policy=policy,
        refund_amount=refund, high_risk=refund >= HIGH_RISK_AMOUNT,
    )
