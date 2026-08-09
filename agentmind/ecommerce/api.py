"""Mock e-commerce open-platform API (JD-style contract).

Phase 1 stands in for a real order/logistics/after-sales gateway. The tools talk
only to this boundary, so swapping in a real HTTP client later touches nothing
else. All order dates are generated relative to *now* so the demo works whenever
it is run.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _seed_orders(now: datetime) -> list[dict]:
    def mk(
        order_id: str,
        user: str,
        status: str,
        items: list[dict],
        created_days_ago: int,
        shipped_days_ago: int | None = None,
        delivered_days_ago: int | None = None,
        note: str = "",
    ) -> dict:
        total = round(sum(i["qty"] * i["price"] for i in items), 2)
        return {
            "order_id": order_id,
            "user": user,
            "status": status,
            "items": items,
            "total": total,
            "created_at": _fmt(now - timedelta(days=created_days_ago)),
            "shipped_at": _fmt(now - timedelta(days=shipped_days_ago)) if shipped_days_ago is not None else "",
            "delivered_at": _fmt(now - timedelta(days=delivered_days_ago)) if delivered_days_ago is not None else "",
            "address": "广东省深圳市南山区科技园南路 8 号 3 栋 1202",
            "pay_method": "微信支付",
            "note": note,
        }

    return [
        # 已签收 3 天 —— 七天无理由，可退（普通金额）
        mk("JD20260801001", "张三", "已签收",
           [{"name": "小米手环 9 标准版", "qty": 1, "price": 249.0}],
           created_days_ago=9, shipped_days_ago=8, delivered_days_ago=3),
        # 已签收 25 天 —— 超过 15 天最长退货期，不可退
        mk("JD20260801002", "张三", "已签收",
           [{"name": "华为 nova 12 手机（256G）", "qty": 1, "price": 2999.0}],
           created_days_ago=30, shipped_days_ago=29, delivered_days_ago=25),
        # 运输中 —— 未签收，不能售后；物流可查
        mk("JD20260801003", "李四", "运输中",
           [{"name": "罗技 MX Master 3S 鼠标", "qty": 1, "price": 499.0}],
           created_days_ago=2, shipped_days_ago=1),
        # 已签收 2 天 —— 高金额（¥8999），可退但需人工审批
        mk("JD20260801004", "张三", "已签收",
           [{"name": "MacBook Air M3（16G/512G）", "qty": 1, "price": 8999.0}],
           created_days_ago=6, shipped_days_ago=5, delivered_days_ago=2),
        # 已签收 5 天 —— 但已有进行中的售后单，不可重复申请
        mk("JD20260801005", "王五", "已签收",
           [{"name": "公牛 2 米插线板", "qty": 2, "price": 45.0}],
           created_days_ago=12, shipped_days_ago=11, delivered_days_ago=5, note="已有售后单"),
    ]


def _seed_logistics(orders: list[dict], now: datetime) -> dict[str, list[dict]]:
    traces: dict[str, list[dict]] = {}
    for order in orders:
        oid = order["order_id"]
        events: list[dict] = [{"time": order["created_at"], "text": "您的订单已提交，等待商家发货"}]
        if order["shipped_at"]:
            events.append({"time": order["shipped_at"], "text": "【深圳】您的包裹已由 顺丰速运 揽收"})
            events.append({"time": _fmt(now - timedelta(hours=20)), "text": "【深圳分拨中心】快件已到达，正在分拣"})
            events.append({"time": _fmt(now - timedelta(hours=6)), "text": "【深圳南山区】快件运输中，预计今日送达"})
        if order["status"] == "已签收":
            events.append({"time": order["delivered_at"], "text": "【深圳南山区】已签收，感谢使用京东物流"})
        traces[oid] = events
    return traces


class MockEcommerceAPI:
    """In-process stand-in for the e-commerce open platform."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime.now()
        self._orders = {o["order_id"]: o for o in _seed_orders(self._now)}
        self._logistics = _seed_logistics(list(self._orders.values()), self._now)
        self._after_sales: dict[str, list[dict]] = {}
        self._counter = 0
        # seed one in-progress after-sales record (order 05)
        self.create_after_sales(
            "JD20260801005", "七天无理由退货", 90.0, "七天无理由退货", created_at=self._now - timedelta(days=1)
        )

    # ---- orders ------------------------------------------------------
    def lookup_order(self, order_id: str) -> dict | None:
        return self._orders.get(order_id)

    def track_logistics(self, order_id: str) -> list[dict] | None:
        order = self._orders.get(order_id)
        if order is None:
            return None
        return self._logistics.get(order_id, [])

    # ---- after-sales --------------------------------------------------
    def order_after_sales_records(self, order_id: str) -> list[dict]:
        return self._after_sales.get(order_id, [])

    def create_after_sales(
        self,
        order_id: str,
        reason: str,
        refund_amount: float,
        policy: str,
        *,
        created_at: datetime | None = None,
    ) -> dict:
        self._counter += 1
        created = created_at or self._now
        record = {
            "after_sales_id": f"AS{created:%Y%m%d}{self._counter:04d}",
            "order_id": order_id,
            "reason": reason,
            "status": "处理中",
            "refund_amount": round(refund_amount, 2),
            "policy": policy,
            "created_at": _fmt(created),
        }
        self._after_sales.setdefault(order_id, []).append(record)
        if order_id in self._orders:
            self._orders[order_id]["status"] = "售后处理中"
        return record
