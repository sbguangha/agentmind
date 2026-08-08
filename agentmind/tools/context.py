"""Per-turn request context for tools.

The runner binds ``request_context`` for the duration of a turn, giving tools
read-only access to the current emit channel (to surface subagent / approval
events). Mirrors the contextvar pattern used for turn-local permissions.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentmind.core.permissions import ApprovalManager

EmitFn = Callable[[str, dict], Awaitable[None]]

_emit_var: ContextVar[EmitFn | None] = ContextVar("agentmind_emit", default=None)
_approvals_var: ContextVar["ApprovalManager | None"] = ContextVar(
    "agentmind_approvals", default=None
)


@asynccontextmanager
async def request_context(emit: EmitFn, approvals: "ApprovalManager | None" = None):
    """Bind the current turn's emit channel (and approval manager) to this task."""
    token = _emit_var.set(emit)
    atoken = _approvals_var.set(approvals)
    try:
        yield
    finally:
        _emit_var.reset(token)
        _approvals_var.reset(atoken)


def current_emit() -> EmitFn | None:
    """Return the emit channel bound to the current turn, if any."""
    return _emit_var.get()


def current_approvals() -> "ApprovalManager | None":
    return _approvals_var.get()


async def safe_emit(event: str, payload: dict[str, Any]) -> None:
    emit = current_emit()
    if emit is not None:
        await emit(event, payload)
