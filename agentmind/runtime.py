"""Runtime assembly — wiring all subsystems together from settings."""
from __future__ import annotations

from agentmind.config import Settings
from agentmind.core.compressor import Compressor
from agentmind.core.consolidator import MemoryConsolidator
from agentmind.core.permissions import ApprovalManager, build_approval_gate
from agentmind.core.runner import AgentRunner
from agentmind.core.subagent import SubagentManager
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.providers.openai_compat import OpenAICompatProvider
from agentmind.session.manager import SessionManager
from agentmind.tools.datetime_tool import GetCurrentTimeTool
from agentmind.tools.delegate_tool import DelegateTool
from agentmind.tools.filesystem import (
    Filesystem,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from agentmind.tools.memory_tool import RecallTool, RememberTool
from agentmind.tools.registry import ToolRegistry
from agentmind.tools.shell import ShellTool
from agentmind.tools.web import WebFetchTool, WebSearchTool


class AgentRuntime:
    """A fully wired agent: provider + tools + memory + sessions + loop parts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # provider (perception channel to the model)
        self.provider = OpenAICompatProvider(settings.api_base, settings.api_key)

        # memory
        self.memory_store = MemoryStore(settings.resolved_data_dir / "memory.db")
        self.embedder = Embedder(self.provider, settings.embedding_model)
        self.memory = LongTermMemory(self.memory_store, self.embedder)

        # human-in-the-loop approval
        self.approvals = ApprovalManager(timeout=settings.approval_timeout)
        self.gate = build_approval_gate(settings, self.approvals)

        # tools (delegate is registered after the runner exists)
        self.registry = self._build_registry(settings)

        # sessions (short-term memory)
        self.sessions = SessionManager(settings.resolved_data_dir)

        # core
        self.runner = AgentRunner(self.provider, self.registry, self.memory, settings, approval=self.gate)

        # subagent delegation
        self.subagents = SubagentManager(
            self.runner,
            max_depth=settings.max_subagent_depth,
            max_concurrent=settings.max_concurrent_subagents,
        )
        if settings.enable_subagents:
            self.registry.register(DelegateTool(self.subagents))

        # context compression & memory consolidation
        self.compressor = Compressor(self.provider, settings)
        self.consolidator = MemoryConsolidator(self.provider, self.memory, settings)

    def _build_registry(self, settings: Settings) -> ToolRegistry:
        registry = ToolRegistry()
        fs = Filesystem(settings.resolved_workspace)
        registry.register_all(
            GetCurrentTimeTool(),
            ReadFileTool(fs),
            WriteFileTool(fs),
            ListDirectoryTool(fs),
        )
        registry.register(RememberTool(self.memory))
        registry.register(RecallTool(self.memory))
        if settings.allow_shell:
            registry.register(ShellTool(str(settings.resolved_workspace)))
        if settings.enable_web:
            registry.register_all(WebSearchTool(), WebFetchTool())
        return registry

    def use_local_approvals(self) -> None:
        """Route approvals to a terminal prompt instead of the UI (CLI mode)."""
        self.gate.local_prompt = True

    async def shutdown(self) -> None:
        self.approvals.cancel_all()
        await self.provider.close()
