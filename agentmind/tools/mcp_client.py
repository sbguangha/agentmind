"""MCP client: connect to external MCP servers and wrap their tools as native tools.

Lets the standalone agent consume any MCP server (e.g. the ``voice_mcp`` TTS
server) exactly like a built-in tool. Interop pattern: each server's tools are
registered under ``mcp_<server>_<tool>`` and calls are forwarded over stdio
JSON-RPC — the same approach nanobot uses, implemented independently here.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack, suppress
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentmind.config import MCPServerConfig
from agentmind.tools.base import Tool, ToolResult

logger = logging.getLogger("agentmind.mcp")

_CONNECT_TIMEOUT = 30.0


class MCPTool(Tool):
    """A tool backed by a tool exposed by an external MCP server."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        parameters: dict,
        invoke: Any,
    ) -> None:
        self.name = f"mcp_{server_name}_{tool_name}"
        self.description = description
        self.parameters = parameters
        self._invoke = invoke

    async def run(self, **kwargs) -> ToolResult:
        return await self._invoke(kwargs)


class MCPClientManager:
    """Owns the MCP connections and exposes their tools for registration."""

    def __init__(self, servers: dict[str, MCPServerConfig]) -> None:
        self._servers = servers
        self._stacks: list[AsyncExitStack] = []

    @property
    def enabled(self) -> bool:
        return bool(self._servers)

    async def connect_all(self) -> list[Tool]:
        """Connect every configured server and return wrapped tools."""
        tools: list[Tool] = []
        for server_name, cfg in self._servers.items():
            try:
                tools.extend(await self._connect_server(server_name, cfg))
            except Exception as exc:  # noqa: BLE001 - a broken server must not kill the agent
                logger.warning("MCP server '%s' failed to connect: %s", server_name, exc)
        return tools

    async def _connect_server(self, name: str, cfg: MCPServerConfig) -> list[Tool]:
        if not cfg.command:
            logger.warning("MCP server '%s' has no command; skipping", name)
            return []

        env = {**os.environ, **cfg.env}
        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=env,
            cwd=cfg.cwd or None,
        )

        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=_CONNECT_TIMEOUT)

        discovered = await asyncio.wait_for(session.list_tools(), timeout=_CONNECT_TIMEOUT)
        self._stacks.append(stack)

        tools = [
            self._wrap(name, tool, session, cfg.tool_timeout) for tool in discovered.tools
        ]
        logger.info("MCP server '%s': registered %d tool(s)", name, len(tools))
        return tools

    def _wrap(self, server_name: str, tool, session: ClientSession, timeout: float) -> MCPTool:
        async def invoke(arguments: dict[str, Any]) -> ToolResult:
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool.name, arguments=arguments or {}),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return ToolResult(
                    output=f"MCP 工具 {tool.name} 调用超时（>{timeout}s）", is_error=True
                )
            except Exception as exc:  # noqa: BLE001 - surface failures to the model
                return ToolResult(
                    output=f"MCP 工具 {tool.name} 调用失败: {type(exc).__name__}: {exc}",
                    is_error=True,
                )
            return _format_mcp_result(result)

        return MCPTool(
            server_name=server_name,
            tool_name=tool.name,
            description=tool.description or "",
            parameters=tool.inputSchema or {},
            invoke=invoke,
        )

    async def close(self) -> None:
        for stack in reversed(self._stacks):
            with suppress(Exception):
                await stack.aclose()
        self._stacks.clear()


def _format_mcp_result(result) -> ToolResult:
    parts: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        parts.append(str(text) if text is not None else str(item))
    output = "\n".join(parts) if parts else "(无输出)"
    return ToolResult(output=output, is_error=bool(result.isError))
