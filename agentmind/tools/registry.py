"""Tool discovery, registration and safe execution."""
from __future__ import annotations

import json

from agentmind.tools.base import Tool, ToolResult


class ToolRegistry:
    """Holds every available tool and executes invocations safely."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ---- registration --------------------------------------------------
    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def register_all(self, *tools: Tool) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    # ---- model-facing --------------------------------------------------
    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    # ---- execution -----------------------------------------------------
    async def execute(self, name: str, arguments: str) -> ToolResult:
        """Execute ``name`` with the JSON-encoded ``arguments``."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(output=f"未知工具: {name}", is_error=True)

        try:
            kwargs = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return ToolResult(
                output=f"工具参数不是合法 JSON: {exc}",
                data={"name": name, "arguments": arguments},
                is_error=True,
            )

        if not isinstance(kwargs, dict):
            return ToolResult(output="工具参数必须是 JSON 对象", is_error=True)

        try:
            return await tool.run(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any tool failure to the model
            return ToolResult(
                output=f"工具执行出错: {type(exc).__name__}: {exc}",
                data={"name": name, "arguments": kwargs},
                is_error=True,
            )
