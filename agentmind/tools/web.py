"""Perception tool: web search & page fetching (no API key required).

Uses DuckDuckGo Lite's public endpoint so the agent can genuinely *see* the
web out of the box. HTML is parsed with stdlib ``html.parser`` — no
BeautifulSoup dependency needed.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import aiohttp

from agentmind.tools.base import Tool, ToolResult

_SEARCH_URL = "https://lite.duckduckgo.com/lite/"
_MAX_TEXT = 8000


class _TextExtractor(HTMLParser):
    """Collect visible text from an HTML page."""

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
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


class WebSearchTool(Tool):
    name = "web_search"
    description = "使用 DuckDuckGo 搜索互联网并返回结果列表（标题+链接+摘要）。需要联网。"

    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        "required": ["query"],
    }

    async def run(self, query: str, **kwargs) -> ToolResult:
        params = {"q": query}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(_SEARCH_URL, params=params, timeout=20) as resp:
                    if resp.status != 200:
                        return ToolResult(output=f"搜索失败 HTTP {resp.status}", is_error=True)
                    body = await resp.text(errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"网络不可用，无法搜索: {exc}", is_error=True)

        results = _parse_lite_results(body)
        if not results:
            return ToolResult(output="没有找到相关结果。")
        lines = [f"{i + 1}. {title}\n   {url}\n   {snippet}" for i, (title, url, snippet) in enumerate(results)]
        return ToolResult(output="\n\n".join(lines), data={"results": results[:5]})


class WebFetchTool(Tool):
    name = "fetch_webpage"
    description = "抓取一个网页并提取其正文文本（最多 8000 字符）。需要联网。"

    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "完整的网页 URL（含 http/https）"}},
        "required": ["url"],
    }

    async def run(self, url: str, **kwargs) -> ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(output="URL 必须是 http/https 开头。", is_error=True)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status != 200:
                        return ToolResult(output=f"抓取失败 HTTP {resp.status}", is_error=True)
                    body = await resp.text(errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"抓取失败: {exc}", is_error=True)

        extractor = _TextExtractor()
        extractor.feed(body)
        text = extractor.text()
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "\n...[已截断]..."
        return ToolResult(output=text or "(页面无正文文本)")


def _parse_lite_results(body: str) -> list[tuple[str, str, str]]:
    """Parse DuckDuckGo Lite result HTML: (title, url, snippet) triples."""
    results: list[tuple[str, str, str]] = []
    rows = re.findall(
        r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<td class="result-snippet">(.*?)</td>',
        body,
        flags=re.DOTALL,
    )
    for url, title, snippet in rows:
        results.append((_clean(html.unescape(title)), url, _clean(html.unescape(snippet))))
        if len(results) >= 8:
            break
    return results
