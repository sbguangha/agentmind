"""User profile: member level, behavior signals, and stated preferences.

Profiles are derived from the open-platform data (orders + after-sales) plus a
small preference store the agent can extend via ``remember_preference``. This is
what lets the service agent differentiate — a 金卡 member gets VIP handling, a
high-return user gets flagged.
"""
from __future__ import annotations

from agentmind.ecommerce.api import MockEcommerceAPI

_MEMBER_SPEND = [(10000.0, "金卡"), (2000.0, "银卡")]


class UserProfileTracker:
    def __init__(self, api: MockEcommerceAPI) -> None:
        self._api = api
        self._preferences: dict[str, list[str]] = {
            "张三": ["偏好简洁回复", "习惯使用微信支付"],
            "李四": ["更看重物流速度"],
        }

    def profile(self, user: str) -> dict:
        orders = [o for o in self._api.orders() if o["user"] == user]
        total = round(sum(o["total"] for o in orders), 2)
        returns = sum(len(self._api.order_after_sales_records(o["order_id"])) for o in orders)
        level = "普通"
        for threshold, name in _MEMBER_SPEND:
            if total >= threshold:
                level = name
                break
        return {
            "user": user,
            "order_count": len(orders),
            "total_spend": total,
            "member_level": level,
            "return_count": returns,
            "high_return_risk": returns >= 2 and len(orders) >= 3,
            "preferences": list(self._preferences.get(user, [])),
        }

    def remember(self, user: str, note: str) -> None:
        self._preferences.setdefault(user, []).append(note)

    def format(self, profile: dict) -> str:
        lines = [
            f"用户画像：{profile['user']}",
            f"  会员等级：{profile['member_level']}",
            f"  订单数：{profile['order_count']} · 消费总额：¥{profile['total_spend']:,.2f} · 退货次数：{profile['return_count']}",
        ]
        if profile["high_return_risk"]:
            lines.append("  ⚠️ 退货频率较高，属于重点关注用户")
        if profile["preferences"]:
            lines.append("  偏好：" + "、".join(profile["preferences"]))
        return "\n".join(lines)


def profile_hint(api: MockEcommerceAPI, user: str) -> str:
    """A one-line profile hint appended to after-sales results."""
    orders = [o for o in api.orders() if o["user"] == user]
    if not orders:
        return ""
    total = round(sum(o["total"] for o in orders), 2)
    returns = sum(len(api.order_after_sales_records(o["order_id"])) for o in orders)
    level = "普通"
    for threshold, name in _MEMBER_SPEND:
        if total >= threshold:
            level = name
            break
    parts = []
    if level != "普通":
        parts.append(f"用户等级：{level}会员")
    if returns >= 2 and len(orders) >= 3:
        parts.append("⚠️ 退货频率较高")
    return ("\n  👤 " + " · ".join(parts)) if parts else ""
