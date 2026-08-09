"""Human-in-the-loop tool approval.

A tool call becomes *risky* the moment it mutates real state (files, shell).
Before executing such a call the runner consults an :class:`ApprovalGate`:

    * ``auto``      — every tool runs without approval
    * ``ask_risky`` — risky tools require human approval (default)
    * ``ask_all``   — every tool requires human approval

In web mode the gate publishes an ``approval_request`` event to the UI and
waits on :class:`ApprovalManager` for the user's decision (timeout == reject).
In CLI mode it falls back to a direct terminal prompt.
"""
from __future__ import annotations

import asyncio
import uuid

from agentmind.config import Settings
from agentmind.tools.context import current_emit

# Tool names that mutate external state and therefore need a human in the loop.
_RISKY_TOOLS = {"write_file", "run_shell"}


class ApprovalPolicy:
    def __init__(self, mode: str, extra_risky: set[str] | tuple[str, ...] = ()) -> None:
        if mode not in {"auto", "ask_risky", "ask_all"}:
            raise ValueError(f"unknown approval_mode: {mode}")
        self.mode = mode
        self._risky = set(_RISKY_TOOLS) | set(extra_risky)

    def requires(self, tool_name: str) -> bool:
        if self.mode == "auto":
            return False
        if self.mode == "ask_all":
            return True
        return tool_name in self._risky


class ApprovalManager:
    """Routes approval requests to the UI and awaits the user's decision."""

    def __init__(self, timeout: float = 120.0) -> None:
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[bool]] = {}

    def new_request(self, tool_name: str, arguments: str) -> str:
        """Register a pending approval and return its id."""
        self._purge_done()
        approval_id = uuid.uuid4().hex[:12]
        self._pending[approval_id] = asyncio.get_running_loop().create_future()
        return approval_id

    async def wait(self, approval_id: str) -> bool:
        """Await the user's decision; timeout or missing id means reject."""
        future = self._pending.get(approval_id)
        if future is None:
            return False
        if future.done():
            return future.result()
        try:
            return await asyncio.wait_for(future, self._timeout)
        except asyncio.TimeoutError:
            if not future.done():
                future.set_result(False)
            return False

    def respond(self, approval_id: str, approved: bool) -> bool:
        """Resolve a pending approval (returns False if unknown/already done).

        Works whether the response arrives before or after :meth:`wait` started:
        the future stays registered until resolved, and done futures are purged
        lazily on the next request.
        """
        future = self._pending.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(bool(approved))
        return True

    def _purge_done(self) -> None:
        for approval_id, future in list(self._pending.items()):
            if future.done():
                del self._pending[approval_id]

    def cancel_all(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result(False)
        self._pending.clear()


class ApprovalGate:
    """The gate the runner consults before executing a tool call."""

    def __init__(
        self,
        policy: ApprovalPolicy,
        manager: ApprovalManager | None = None,
        *,
        local_prompt: bool = False,
    ) -> None:
        self._policy = policy
        self._manager = manager
        self.local_prompt = local_prompt

    @classmethod
    def auto(cls) -> "ApprovalGate":
        """A gate that never blocks (used by subagents and tests)."""
        return cls(ApprovalPolicy("auto"))

    async def request(self, tool_name: str, arguments: str) -> bool:
        """Return True when the tool call may proceed."""
        if not self._policy.requires(tool_name):
            return True
        if self._manager is not None and not self.local_prompt:
            return await self._request_remote(tool_name, arguments)
        return await self._request_local(tool_name, arguments)

    async def _request_remote(self, tool_name: str, arguments: str) -> bool:
        approval_id = self._manager.new_request(tool_name, arguments)
        emit = current_emit()
        if emit is not None:
            await emit(
                "approval_request",
                {"approval_id": approval_id, "tool": tool_name, "arguments": arguments},
            )
        approved = await self._manager.wait(approval_id)
        if emit is not None:
            await emit("approval_result", {"approval_id": approval_id, "approved": approved})
        return approved

    async def _request_local(self, tool_name: str, arguments: str) -> bool:
        prompt = f"\n[审批] 允许调用 {tool_name}({arguments}) 吗？[y/N] "
        answer = await asyncio.to_thread(input, prompt)
        return answer.strip().lower() in {"y", "yes"}


def build_approval_gate(
    settings: Settings,
    manager: ApprovalManager | None,
    extra_risky: set[str] | tuple[str, ...] = (),
) -> ApprovalGate:
    return ApprovalGate(ApprovalPolicy(settings.approval_mode, extra_risky=extra_risky), manager)
