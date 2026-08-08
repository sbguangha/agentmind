"""AgentRunner — the ReAct reasoning + acting loop.

Each user turn runs a *reason -> act -> observe* cycle:

    1. perceive  : build context (system prompt + long-term memory + history)
    2. reason    : ask the model, streaming its reasoning text live
    3. act       : if the model requests tool calls, gate them through human
                   approval (when configured), execute them, feed the
                   observations back, and loop
    4. answer    : when the model replies with plain text, that is the answer

Guards:
    * ``max_tool_rounds`` — a tool that keeps getting called cannot spin forever
    * approval gate       — risky tools pause for a human decision
    * request context     — tools get a read-only emit channel for this turn
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from agentmind.config import Settings
from agentmind.core.context import build_system_prompt
from agentmind.core.permissions import ApprovalGate
from agentmind.memory.long_term import LongTermMemory
from agentmind.providers.base import LLMProvider, ToolCall
from agentmind.session.types import Session
from agentmind.tools.base import ToolResult
from agentmind.tools.context import request_context
from agentmind.tools.registry import ToolRegistry

EmitFn = Callable[[str, dict], Awaitable[None]]


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        memory: LongTermMemory,
        settings: Settings,
        approval: ApprovalGate | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._memory = memory
        self._settings = settings
        self._approval = approval or ApprovalGate.auto()

    # ------------------------------------------------------------------
    async def run_turn(
        self,
        session: Session,
        user_text: str,
        emit: EmitFn,
        *,
        system_prompt: str | None = None,
    ) -> str:
        """Process one user message, returning the final assistant answer."""
        async with request_context(emit):
            return await self._run_turn(session, user_text, emit, system_prompt=system_prompt)

    async def _run_turn(
        self,
        session: Session,
        user_text: str,
        emit: EmitFn,
        *,
        system_prompt: str | None,
    ) -> str:
        # 1) perceive — recall long-term memories relevant to this question
        memories = await self._memory.recall_text(user_text, self._settings.memory_top_k)

        # 2) assemble the working message list for this turn
        prompt = build_system_prompt(self._settings, memories, prompt=system_prompt)
        messages: list[dict] = [{"role": "system", "content": prompt}]
        for msg in session.context_window(self._settings.max_history_chars):
            messages.append(msg.to_api())
        messages.append({"role": "user", "content": user_text})

        # 3) reason + act loop
        for _ in range(self._settings.max_tool_rounds):
            content, calls = await self._request(messages, emit)
            await emit("thinking_end", {})

            if not calls:
                return content or ""

            # act — gate, then execute every requested tool
            observations: list[tuple[ToolCall, str]] = []
            for call in calls:
                arguments = _pretty_args(call.arguments)
                await emit("tool_start", {"name": call.name, "arguments": arguments})

                approved = await self._approval.request(call.name, arguments)
                if approved:
                    result = await self._registry.execute(call.name, call.arguments)
                else:
                    result = ToolResult(output="用户拒绝了该工具调用。", is_error=True)
                await emit("tool_end", {"name": call.name, "arguments": arguments, "output": result.output[:2000]})
                observations.append((call, result.output))

            # feed the round-trip back into context for the next reason step
            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call, _ in observations
                    ],
                }
            )
            for call, output in observations:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

        raise RuntimeError(f"达到最大工具轮数限制（{self._settings.max_tool_rounds}）")

    # ------------------------------------------------------------------
    async def _request(self, messages: list[dict], emit: EmitFn) -> tuple[str, list[ToolCall]]:
        """Stream one model request, forwarding text deltas to the UI."""
        await emit("thinking_start", {})
        content_parts: list[str] = []
        calls: dict[int, ToolCall] = {}

        async for chunk in self._provider.stream(
            messages,
            self._settings.model,
            tools=self._registry.schemas(),
            temperature=self._settings.temperature,
        ):
            if chunk.content_delta:
                content_parts.append(chunk.content_delta)
                await emit("delta", {"text": chunk.content_delta})
            for delta in chunk.tool_calls:
                call = calls.setdefault(
                    delta.index,
                    ToolCall(
                        id=delta.id or f"call_{delta.index}",
                        name=delta.name or "",
                        arguments="",
                    ),
                )
                if delta.id:
                    call.id = delta.id
                if delta.name:
                    call.name = delta.name
                call.arguments += delta.arguments

        return "".join(content_parts), list(calls.values())


def _pretty_args(arguments: str) -> str:
    try:
        data = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    lines = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in data.items()]
    return ", ".join(lines) if lines else "{}"
