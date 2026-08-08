"""Tests for the MCP client: launch a real (fake) MCP server as a subprocess,
connect over stdio and verify its tools are wrapped and callable."""
from __future__ import annotations

import base64
import sys

import pytest

from agentmind.config import MCPServerConfig
from agentmind.tools.context import request_context
from agentmind.tools.mcp_client import MCPClientManager

_FAKE_SERVER = """
import base64
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-server")

@mcp.tool()
def echo(text: str) -> str:
    return f"echo:{text}"

@mcp.tool()
def boom() -> str:
    raise ValueError("server exploded")

@mcp.tool()
def audio() -> str:
    return "已生成语音（zh-CN-XiaoxiaoNeural）：你好\\nAUDIO:audio/mpeg:" + base64.b64encode(b"MP3DATA").decode()

if __name__ == "__main__":
    mcp.run(transport="stdio")
"""


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return script


async def test_connect_and_wrap_tools(fake_server):
    manager = MCPClientManager(
        {
            "fake": MCPServerConfig(
                command=sys.executable,
                args=[str(fake_server)],
                env={"PYTHONIOENCODING": "utf-8"},
            )
        }
    )
    try:
        tools = await manager.connect_all()
        names = {t.name for t in tools}
        assert "mcp_fake_echo" in names
        assert "mcp_fake_boom" in names

        echo = next(t for t in tools if t.name == "mcp_fake_echo")
        assert echo.parameters.get("properties", {}).get("text") is not None

        result = await echo.run(text="hello")
        assert result.output == "echo:hello"
        assert result.is_error is False

        boom = next(t for t in tools if t.name == "mcp_fake_boom")
        result = await boom.run()
        assert result.is_error is True
        assert "exploded" in result.output
    finally:
        await manager.close()


async def test_audio_attachment_emitted_not_in_model_text(fake_server):
    """Audio returned by an MCP tool is forwarded to the UI as an event and
    stripped from the model-facing output (keeps LLM context clean)."""
    manager = MCPClientManager(
        {
            "fake": MCPServerConfig(
                command=sys.executable,
                args=[str(fake_server)],
                env={"PYTHONIOENCODING": "utf-8"},
            )
        }
    )
    try:
        tools = await manager.connect_all()
        audio_tool = next(t for t in tools if t.name == "mcp_fake_audio")

        emitted = []

        async def emit(event, payload):
            emitted.append((event, payload))

        async with request_context(emit):
            result = await audio_tool.run()

        assert result.output == "已生成语音（zh-CN-XiaoxiaoNeural）：你好"
        assert "AUDIO:" not in result.output  # base64 not fed to the model
        assert any(e == "attachment" for e, _ in emitted)
        attach = next(p for e, p in emitted if e == "attachment")
        assert attach["mime"] == "audio/mpeg"
        assert base64.b64decode(attach["data"]) == b"MP3DATA"
    finally:
        await manager.close()


async def test_broken_server_does_not_raise(fake_server, tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("raise SystemExit(1)", encoding="utf-8")
    manager = MCPClientManager(
        {"bad": MCPServerConfig(command=sys.executable, args=[str(broken)])}
    )
    try:
        tools = await manager.connect_all()  # must not raise
        assert tools == []
    finally:
        await manager.close()


async def test_missing_command_skipped():
    manager = MCPClientManager({"none": MCPServerConfig()})
    assert await manager.connect_all() == []


def test_disabled_by_default():
    from agentmind.config import Settings

    settings = Settings()
    assert settings.mcp_servers == {}
