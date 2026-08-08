"""The delegate tool — exposes subagent delegation to the model."""
from __future__ import annotations

from agentmind.core.subagent import SubagentManager
from agentmind.tools.base import Tool, ToolResult
from agentmind.tools.context import current_emit


class DelegateTool(Tool):
    name = "delegate"
    description = (
        "把一个自包含的子任务委派给独立上下文运行的子代理执行。子代理会像你一样使用工具，"
        "自主完成并返回结果。适用于：可拆分的独立子问题——例如独立调研某个主题、编写某个模块、"
        "分析某段数据。任务描述必须自包含（含目标、约束、期望输出），不要依赖父对话上下文。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "自包含的子任务描述"},
            "context": {"type": "string", "description": "可选：补充背景信息"},
        },
        "required": ["task"],
    }

    def __init__(self, subagents: SubagentManager) -> None:
        self._subagents = subagents

    async def run(self, task: str, context: str = "", **kwargs) -> ToolResult:
        emit = current_emit()
        if emit is not None:
            await emit("subagent_start", {"task": task})

        ok, result = await self._subagents.delegate(task, context)

        if emit is not None:
            await emit("subagent_end", {"task": task, "result": result, "success": ok})
        if not ok:
            return ToolResult(output=f"子代理委派失败：{result}", is_error=True)
        return ToolResult(output=result)
