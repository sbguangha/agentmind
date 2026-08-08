"""Run the voice_speak MCP server as a stdio child and call the tool once.

A stdio MCP server is meant to be driven by a client, not run interactively —
this tiny client is that driver. On a machine with speakers it will actually
speak the text.

Usage (from the voice_mcp directory):
    uv run python demo_client.py "你好，测试语音"
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(errors="replace")

DEFAULT_TEXT = "你好，我是 AgentMind 的语音工具，测试通过。"


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEXT
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "voice_mcp_server"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("discovered tools:", [t.name for t in tools.tools])

            print(f"calling voice_speak(text={text!r}, voice=xiaoxiao) ...")
            result = await session.call_tool("voice_speak", {"text": text, "voice": "xiaoxiao"})
            for content in result.content:
                print("result:", getattr(content, "text", content))


if __name__ == "__main__":
    asyncio.run(main())
