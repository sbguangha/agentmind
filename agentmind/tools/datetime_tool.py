"""Perception tool: the current time and date.

The simplest demonstration that the agent senses its environment rather than
guessing — a common trap for naive agents is inventing dates and times.
"""
from __future__ import annotations

from datetime import datetime

from agentmind.tools.base import Tool, ToolResult


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = (
        "获取当前的日期、时间与时区。当需要知道'现在几点/今天星期几/当前日期'，"
        "或任何依赖当前时间的信息时使用。"
    )
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs) -> ToolResult:
        now = datetime.now()
        return ToolResult(
            output=(
                f"当前时间: {now:%Y-%m-%d %H:%M:%S} ({now:%A})，"
                f"第 {now.isocalendar().week} 周"
            ),
            data={"iso": now.isoformat(timespec="seconds")},
        )
