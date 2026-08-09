"""After-sales policy knowledge base (RAG-style keyword retrieval).

Policies live here as small structured documents; the ``after_sales_policy``
tool retrieves the relevant ones for a question. This keeps the domain rules
queryable instead of baked into the model's memory.
"""
from __future__ import annotations

import re

POLICIES: list[dict] = [
    {
        "topic": "七天无理由退货",
        "keywords": ["七天无理由", "无理由退货", "七天", "7天", "退换货", "不想要"],
        "text": "签收后 7 天内，非定制、非生鲜、非贴身类商品支持「七天无理由退货」，无需说明理由；寄回运费由买家承担（有运费险则由保险赔付）。",
    },
    {
        "topic": "15 天质量问题换货",
        "keywords": ["质量问题", "换货", "15天", "十五天", "故障", "坏了", "坏了质量"],
        "text": "签收后 15 天内，因商品质量问题（性能故障/功能缺失/破损）支持换货；因质量问题的退换货运费由卖家承担。",
    },
    {
        "topic": "退货运费",
        "keywords": ["运费", "邮费", "谁出", "运费险", "运费保险"],
        "text": "七天无理由退货的寄回运费由买家承担；质量问题退换货运费由卖家承担。部分商品附赠「运费险」，理赔金额通常为 8~25 元。",
    },
    {
        "topic": "退款时效",
        "keywords": ["退款", "多久到账", "到账", "几天", "时效", "原路", "什么时候到"],
        "text": "售后审核一般 1~3 个工作日完成，通过后退款按原支付方式原路退回；到账时间取决于支付渠道（微信/支付宝通常实时到账，银行卡 1~3 个工作日）。",
    },
    {
        "topic": "不支持退货的商品",
        "keywords": ["不支持", "不能退", "生鲜", "定制", "贴身", "内裤", "食品", "激活", "数码"],
        "text": "以下商品不支持七天无理由退货：定制类、生鲜食品、贴身衣物（内衣内裤）、已激活的数码产品、拆封的影音/软件商品等；质量问题仍可走售后。",
    },
    {
        "topic": "发票",
        "keywords": ["发票", "开票", "报销", "红字"],
        "text": "退货完成后，已开具的电子发票将作废，可申请重新开具或开具红字发票；纸质发票需随商品寄回。",
    },
    {
        "topic": "售后流程",
        "keywords": ["流程", "怎么退", "步骤", "申请", "操作"],
        "text": "售后流程：①在订单详情申请售后 ②平台审核（1~3 个工作日）③按退单号寄回商品 ④质检通过后退款。",
    },
]


def _tokens(text: str) -> set[str]:
    text = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.update(cjk)
    for a, b in zip(cjk, cjk[1:]):
        tokens.add(a + b)
    return tokens


def search_policies(query: str, top_k: int = 3) -> list[dict]:
    """Return the policies most relevant to *query* (bigram/latin scoring).

    Only tokens of length >= 2 count, so single CJK characters (noise) can
    never match.
    """
    q_tokens = {t for t in _tokens(query) if len(t) >= 2}
    scored: list[tuple[dict, int]] = []
    for policy in POLICIES:
        haystack = _tokens(policy["topic"] + " " + " ".join(policy["keywords"]))
        hits = q_tokens & haystack
        score = sum(2 if len(t) > 1 else 1 for t in hits)
        scored.append((policy, score))
    scored.sort(key=lambda item: -item[1])
    return [policy for policy, score in scored if score > 0][:top_k]
