"""Perception tool: web search & page fetching.

Search uses a **multi-provider architecture** (mirroring nanobot's
``WebSearchTool``): the configured provider is tried first, then a fallback
chain, so a blocked/unreliable engine never kills the feature.

    bing (default, no key, works in CN via RSS) · duckduckgo · bocha ·
    volcengine · tavily · brave · serper · jina

Fetching is SSRF-protected: every URL (and every redirect hop) is validated
against private/loopback targets before the request is made.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp

from agentmind.security.network import validate_url
from agentmind.tools.base import Tool, ToolResult

_MAX_TEXT = 8000
_MAX_REDIRECTS = 5
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

# ---- shared helpers -----------------------------------------------------


class _TextExtractor(HTMLParser):
    """Collect visible text from an HTML page (stdlib, no deps)."""

    SKIP = {"script", "style", "noscript", "svg", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", html.unescape(text)).strip()


def _format_results(query: str, items: list[dict], n: int) -> ToolResult:
    items = [i for i in items if i.get("title") or i.get("url")][:n]
    if not items:
        return ToolResult(output=f"没有找到与「{query}」相关的结果。")
    lines = [f"搜索结果：{query}", ""]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item.get('title', '')}")
        lines.append(f"   {item.get('url', '')}")
        snippet = _clean(item.get("content", ""))
        if snippet:
            lines.append(f"   {snippet}")
    return ToolResult(output="\n".join(lines), data={"results": items[:n]})


# ---- search backends ----------------------------------------------------


async def _search_bing(session, query: str, n: int) -> list[dict]:
    """Bing RSS feed — no API key, reliable, works in mainland China."""
    url = "https://www.bing.com/search"
    params = {"format": "rss", "q": query}
    async with session.get(url, params=params, timeout=20, headers={"User-Agent": _USER_AGENT}) as resp:
        resp.raise_for_status()
        body = await resp.text(errors="replace")
    return _parse_rss(body)[:n]


def _parse_rss(body: str) -> list[dict]:
    """Parse an RSS 2.0 feed into [{title, url, content}] (Bing search output)."""
    items: list[dict] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return items
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "")
        if title and link:
            items.append({"title": _clean(title), "url": link, "content": _clean(desc)})
    return items


async def _search_duckduckgo(session, query: str, n: int) -> list[dict]:
    """DuckDuckGo Lite HTML — no key, but unreliable in mainland China."""
    async with session.get(
        "https://lite.duckduckgo.com/lite/", params={"q": query}, timeout=20
    ) as resp:
        resp.raise_for_status()
        body = await resp.text(errors="replace")
    items: list[dict] = []
    rows = re.findall(
        r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<td class="result-snippet">(.*?)</td>',
        body,
        flags=re.DOTALL,
    )
    for url, title, snippet in rows:
        items.append({"title": _clean(html.unescape(title)), "url": url, "content": _clean(html.unescape(snippet))})
        if len(items) >= n:
            break
    return items


async def _search_bocha(session, query: str, n: int, api_key: str) -> list[dict]:
    """博查 (Bocha) — Chinese search API, requires key."""
    async with session.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query, "summary": True, "count": n, "freshness": "noLimit"},
        timeout=20,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    return [
        {"title": p.get("name", ""), "url": p.get("url", ""), "content": p.get("summary") or p.get("snippet", "")}
        for p in pages
    ]


async def _search_volcengine(session, query: str, n: int, api_key: str) -> list[dict]:
    """火山引擎/豆包 (Volcengine) — Chinese search API, requires key."""
    async with session.post(
        "https://open.feedcoopapi.com/search_api/web_search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"Query": query, "SearchType": "web", "Count": n, "NeedSummary": True},
        timeout=20,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    result = data.get("Result") or data
    web_results = result.get("WebResults") or result.get("webResults") or result.get("results") or []
    items = []
    for r in web_results:
        meta = " | ".join(
            str(p) for p in (r.get("SiteName"), r.get("PublishTime")) if p
        )
        content = r.get("Summary") or r.get("summary") or r.get("Snippet") or ""
        items.append(
            {
                "title": r.get("Title") or r.get("title") or "",
                "url": r.get("Url") or r.get("url") or "",
                "content": "\n".join(p for p in (meta, content) if p),
            }
        )
    return items


async def _search_tavily(session, query: str, n: int, api_key: str) -> list[dict]:
    async with session.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"query": query, "max_results": n},
        timeout=20,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
        for r in data.get("results", [])
    ]


async def _search_brave(session, query: str, n: int, api_key: str) -> list[dict]:
    async with session.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": n},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        timeout=20,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])
    ]


async def _search_serper(session, query: str, n: int, api_key: str) -> list[dict]:
    async with session.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": n},
        timeout=20,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""), "content": r.get("snippet", "")}
        for r in data.get("organic", [])
    ]


async def _search_jina(session, query: str, n: int, api_key: str) -> list[dict]:
    from urllib.parse import quote

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with session.get(
        f"https://s.jina.ai/{quote(query, safe='')}", headers=headers, timeout=20
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return [
        {"title": d.get("title", ""), "url": d.get("url", ""), "content": d.get("content", "")[:500]}
        for d in data.get("data", [])[:n]
    ]


# ---- WebSearchTool ------------------------------------------------------

_SEARCH_HANDLERS = {
    "bing": _search_bing,
    "duckduckgo": _search_duckduckgo,
    "bocha": _search_bocha,
    "volcengine": _search_volcengine,
    "tavily": _search_tavily,
    "brave": _search_brave,
    "serper": _search_serper,
    "jina": _search_jina,
}
_KEY_PROVIDERS = {"bocha", "volcengine", "tavily", "brave", "serper"}


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "搜索互联网并返回结果列表（标题+链接+摘要）。多引擎自动降级，无需 API key 也能用（Bing）。"
        "需要联网。"
    )

    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        "required": ["query"],
    }

    def __init__(self, provider: str = "bing", api_key: str = "", max_results: int = 5) -> None:
        self._provider = provider.strip().lower() or "bing"
        self._api_key = api_key
        self._max_results = min(max(max_results, 1), 10)

    async def run(self, query: str, **kwargs) -> ToolResult:
        errors: list[str] = []
        try:
            async with aiohttp.ClientSession() as session:
                for provider in self._ordered_providers():
                    try:
                        items = await self._search_one(session, provider, query)
                        if items:
                            return _format_results(query, items, self._max_results)
                    except Exception as exc:  # noqa: BLE001 - try the next engine
                        errors.append(f"{provider}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"网络不可用，无法搜索: {exc}", is_error=True)
        detail = "；".join(errors[:3])
        return ToolResult(
            output=f"搜索服务暂时不可用（已尝试多个引擎: {detail}）。请稍后再试。", is_error=True
        )

    def _ordered_providers(self) -> list[str]:
        chain = [self._provider] if self._provider in _SEARCH_HANDLERS else []
        for name in ("bing", "duckduckgo"):
            if name not in chain:
                chain.append(name)
        for name in _KEY_PROVIDERS:
            if name not in chain and (self._api_key or _env_key(name)):
                chain.append(name)
        return chain

    async def _search_one(self, session, provider: str, query: str) -> list[dict]:
        handler = _SEARCH_HANDLERS[provider]
        if provider in _KEY_PROVIDERS:
            key = self._api_key or _env_key(provider)
            if not key:
                return []
            return await handler(session, query, self._max_results, key)
        if provider == "jina":
            key = self._api_key or _env_key("jina")
            return await handler(session, query, self._max_results, key)
        return await handler(session, query, self._max_results)


def _env_key(provider: str) -> str:
    import os

    return os.environ.get(f"{provider.upper()}_API_KEY", "")


# ---- WebFetchTool -------------------------------------------------------


async def _fetch_safe(client: aiohttp.ClientSession, url: str, *, allow_loopback: bool) -> tuple[bytes | None, str, dict | None]:
    """GET *url* with SSRF validation on every redirect hop. Returns (content, final_url, headers)."""
    current = url
    headers = {"User-Agent": _USER_AGENT}
    for _ in range(_MAX_REDIRECTS + 1):
        ok, error = await validate_url(current, allow_loopback=allow_loopback)
        if not ok:
            return None, current, {"error": f"URL 校验失败: {error}"}
        async with client.get(current, headers=headers, allow_redirects=False, timeout=30) as resp:
            if 300 <= resp.status < 400:
                location = resp.headers.get("location")
                if not location:
                    return await resp.read(), str(resp.url), dict(resp.headers)
                current = urljoin(str(resp.url), location)
                continue
            if resp.status != 200:
                return None, current, {"error": f"HTTP {resp.status}"}
            return await resp.read(), str(resp.url), dict(resp.headers)
    return None, current, {"error": f"重定向超过 {_MAX_REDIRECTS} 次"}


class WebFetchTool(Tool):
    name = "fetch_webpage"
    description = "抓取一个网页并提取其正文文本（最多 8000 字符）。带 SSRF 防护，需要联网。"

    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "完整的网页 URL（含 http/https）"}},
        "required": ["url"],
    }

    def __init__(self, allow_loopback: bool = False) -> None:
        self._allow_loopback = allow_loopback

    async def run(self, url: str, **kwargs) -> ToolResult:
        url = url.strip(" \t\r\n`\"'")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(output="URL 必须是 http/https 开头。", is_error=True)
        try:
            async with aiohttp.ClientSession() as session:
                content, final_url, meta = await _fetch_safe(
                    session, url, allow_loopback=self._allow_loopback
                )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"抓取失败: {exc}", is_error=True)

        if meta and meta.get("error"):
            return ToolResult(output=f"抓取失败: {meta['error']}", is_error=True)
        if content is None:
            return ToolResult(output="抓取失败: 无内容返回。", is_error=True)

        text = _extract_text(content)
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "\n...[已截断]..."
        return ToolResult(output=text or "(页面无正文文本)", data={"url": final_url})


def _extract_text(content: bytes) -> str:
    raw = _decode(content)
    lowered = raw[:512].lower()
    if not lowered.lstrip().startswith(("<html", "<!doctype", "<div", "<head", "<body")):
        return re.sub(r"\n{3,}", "\n\n", raw).strip()
    extractor = _TextExtractor()
    try:
        extractor.feed(raw)
    except Exception:  # noqa: BLE001
        return raw
    return extractor.text()


def _decode(content: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")
