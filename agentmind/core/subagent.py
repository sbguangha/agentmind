"""Subagent delegation — complex-task decomposition.

A subagent is a *fresh, isolated* agent run: it gets its own context (no
parent history), a focused system prompt, and the same tool registry. This
is how the agent decomposes large tasks: delegate a self-contained subtask
to a subagent, let it work autonomously, and collect a distilled result
back into the parent context (which stays small).

Guards, mirroring nanobot's SubagentManager:
    * depth limit  — a subagent cannot recursively spawn without bound
    * concurrency  — bounded simultaneous subagents via a semaphore
"""
from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar

from agentmind.core.runner import AgentRunner
from agentmind.session.types import Session

_SUBAGENT_DEPTH: ContextVar[int] = ContextVar("agentmind_subagent_depth", default=0)

_SUBAGENT_SYSTEM_PROMPT = (
    "你是被父智能体委派执行子任务的子代理（Subagent）。\n"
    "你的职责：独立完成下面的任务。你可以像主智能体一样使用工具（时间/文件/网络/记忆等），"
    "但你的上下文是全新独立的。\n"
    "完成后用简洁、结构化的中文汇报结果；如果失败，如实说明原因。不要客套，不要输出过程噪音。\n\n"
    "任务：{task}"
)


class SubagentManager:
    def __init__(
        self,
        runner: AgentRunner,
        *,
        max_depth: int = 2,
        max_concurrent: int = 4,
    ) -> None:
        self._runner = runner
        self._max_depth = max_depth
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = 0

    @property
    def running(self) -> int:
        return self._running

    async def delegate(self, task: str, context: str = "") -> tuple[bool, str]:
        """Run a subagent synchronously; returns (success, result_text)."""
        depth = _SUBAGENT_DEPTH.get()
        if depth >= self._max_depth:
            return False, f"子代理层级已达上限（{self._max_depth}），请把任务拆得更小或由主智能体直接完成。"

        full_task = task if not context else f"{task}\n\n背景信息：{context}"
        async with self._semaphore:
            token = _SUBAGENT_DEPTH.set(depth + 1)
            self._running += 1
            try:
                session = Session(id=f"subagent-{uuid.uuid4().hex[:8]}")
                activity: list[str] = []

                async def sub_emit(event: str, payload: dict) -> None:
                    if event == "tool_start":
                        activity.append(f"[{payload['name']}({payload['arguments']})]")
                    elif event == "tool_end" and payload.get("output"):
                        activity.append(payload["output"][:300])

                answer = await self._runner.run_turn(
                    session,
                    full_task,
                    sub_emit,
                    system_prompt=_SUBAGENT_SYSTEM_PROMPT.format(task=full_task),
                )
                if not answer.strip():
                    return False, "子代理未返回有效结果。"
                return True, answer
            finally:
                self._running -= 1
                _SUBAGENT_DEPTH.reset(token)
