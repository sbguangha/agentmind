"""Workspace access scope — the agent's filesystem permission boundary.

Migrated from nanobot's ``security/workspace_access.py`` (MIT):

    * ``restricted`` — tools may only touch the configured workspace
    * ``full``       — tools may touch the whole filesystem (dangerous)

The scope is bound per turn via a contextvar; tools read :func:`current_scope`
to decide their own sandbox. The effective scope for a session comes from the
session's access_mode (if set) or the global default.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AccessMode = Literal["restricted", "full"]
_ACCESS_MODES = {"restricted", "full"}

_CURRENT_SCOPE: ContextVar["WorkspaceScope | None"] = ContextVar(
    "agentmind_workspace_scope", default=None
)


@dataclass(frozen=True)
class WorkspaceScope:
    """Effective filesystem boundary for one agent turn."""

    project_path: Path
    access_mode: AccessMode

    @property
    def restrict(self) -> bool:
        return self.access_mode == "restricted"


class WorkspaceScopeError(ValueError):
    pass


def build_scope(project_path: str | Path, access_mode: str) -> WorkspaceScope:
    mode = access_mode.strip().lower().replace("_", "-")
    if mode not in _ACCESS_MODES:
        raise WorkspaceScopeError("access_mode must be 'restricted' or 'full'")
    return WorkspaceScope(
        project_path=Path(project_path).expanduser().resolve(strict=False),
        access_mode=mode,  # type: ignore[arg-type]
    )


def bind_scope(scope: WorkspaceScope) -> Token:
    return _CURRENT_SCOPE.set(scope)


def reset_scope(token: Token) -> None:
    _CURRENT_SCOPE.reset(token)


def current_scope() -> WorkspaceScope | None:
    return _CURRENT_SCOPE.get()


class WorkspaceScopeResolver:
    """Resolve the effective scope for a session, honoring per-session override."""

    def __init__(self, default_workspace: str | Path, default_restrict: bool = True) -> None:
        self._default_workspace = Path(default_workspace)
        self._default_mode: AccessMode = "restricted" if default_restrict else "full"

    def default(self) -> WorkspaceScope:
        return build_scope(self._default_workspace, self._default_mode)

    def resolve(self, session_access_mode: str | None = None) -> WorkspaceScope:
        mode = session_access_mode if session_access_mode in _ACCESS_MODES else None
        return build_scope(self._default_workspace, mode or self._default_mode)
