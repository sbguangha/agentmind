"""Agentic memory tools: the model can explicitly remember and recall.

This turns memory from a passive side effect into an *active capability*:
the agent decides when a fact is worth persisting, and reaches back for it
later — a hallmark of a mature agent.
"""
from __future__ import annotations

from datetime import datetime

from agentmind.memory.long_term import LongTermMemory
from agentmind.tools.base import Tool, ToolResult


class RememberTool(Tool):
    name = "remember"
    description = (
        "把一条重要的信息/事实写入长期记忆，供以后（包括其他会话）回忆。"
        "当用户明确要求'记住/别忘了'，或你发现值得跨会话保留的信息时使用。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容（简洁、自包含）"},
        },
        "required": ["content"],
    }

    def __init__(self, memory: LongTermMemory) -> None:
        self._memory = memory

    async def run(self, content: str, **kwargs) -> ToolResult:
        entry = await self._memory.remember(content, kind="fact")
        return ToolResult(output=f"已记住（id={entry.id}）：{content}")


class RecallTool(Tool):
    name = "recall"
    description = (
        "在长期记忆中检索与查询最相关的内容（语义相似度/关键词匹配）。"
        "当需要回忆之前聊过的内容、用户偏好或跨会话信息时使用。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要回忆的主题或关键词"},
            "top_k": {"type": "integer", "description": "返回条数，默认 3"},
        },
        "required": ["query"],
    }

    def __init__(self, memory: LongTermMemory) -> None:
        self._memory = memory

    async def run(self, query: str, top_k: int = 3, **kwargs) -> ToolResult:
        hits = await self._memory.recall(query, top_k=min(max(top_k, 1), 10))
        if not hits:
            return ToolResult(output="没有找到相关记忆。")
        blocks = [
            f"- [{e.kind} {datetime.fromtimestamp(e.created_at).strftime('%m-%d %H:%M')}] {e.content}"
            for e in hits
        ]
        return ToolResult(output="找到以下记忆：\n" + "\n".join(blocks))
