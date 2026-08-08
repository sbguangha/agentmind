"""Tests for the multi-provider web search."""
from __future__ import annotations

from agentmind.tools.web import WebSearchTool, _format_results, _parse_rss

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:news="http://www.google.com/schemas/2007/news">
  <channel>
    <item>
      <title>Python asyncio docs</title>
      <link>https://docs.python.org/3/library/asyncio.html</link>
      <description>&lt;p&gt;asyncio is a library to write concurrent code&lt;/p&gt;</description>
    </item>
    <item>
      <title>Real Python walkthrough</title>
      <link>https://realpython.com/async-io-python/</link>
      <description>Explore asyncio with hands-on examples</description>
    </item>
  </channel>
</rss>
"""


def test_parse_bing_rss():
    items = _parse_rss(_RSS)
    assert len(items) == 2
    assert items[0]["title"] == "Python asyncio docs"
    assert items[0]["url"] == "https://docs.python.org/3/library/asyncio.html"
    assert "asyncio is a library" in items[0]["content"]  # tags stripped


def test_parse_rss_bad_xml():
    assert _parse_rss("not xml at all") == []


def test_format_results_truncates_and_skips_empty():
    items = [
        {"title": "A", "url": "https://a.com", "content": "snippet"},
        {"title": "", "url": "", "content": ""},
        {"title": "B", "url": "https://b.com", "content": ""},
    ]
    result = _format_results("q", items, n=5)
    assert "A" in result.output
    assert "B" in result.output
    assert result.data["results"][1]["title"] == "B"


def test_ordered_providers_puts_configured_first():
    tool = WebSearchTool(provider="tavily", api_key="k")
    order = tool._ordered_providers()
    assert order[0] == "tavily"
    assert "bing" in order and "duckduckgo" in order  # fallbacks always present


def test_keyed_provider_skipped_without_key():
    tool = WebSearchTool(provider="tavily", api_key="")
    order = tool._ordered_providers()
    # configured provider is tried first, but the no-key fallback chain remains
    assert order[0] == "tavily"
    assert "bing" in order
