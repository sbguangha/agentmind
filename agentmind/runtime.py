"""Runtime assembly — wiring all subsystems together from settings."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

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
from agentmind.security.workspace_access import WorkspaceScopeResolver
from agentmind.session.manager import SessionManager
from agentmind.tools.context import EmitFn, request_context
from agentmind.tools.datetime_tool import GetCurrentTimeTool
from agentmind.tools.delegate_tool import DelegateTool
from agentmind.tools.filesystem import (
    Filesystem,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from agentmind.tools.mcp_client import MCPClientManager
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
        extra_risky = {"after_sales_apply"} if settings.enable_ecommerce else set()
        self.gate = build_approval_gate(settings, self.approvals, extra_risky=extra_risky)

        # permission boundary (workspace access scope)
        self.scope_resolver = WorkspaceScopeResolver(
            settings.resolved_workspace,
            default_restrict=settings.workspace_access == "restricted",
        )

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

        # MCP client (external MCP servers -> native tools)
        self.mcp = MCPClientManager(settings.mcp_servers)

    async def startup(self) -> None:
        """Connect MCP servers and register their tools (call before the loop runs)."""
        if self.mcp.enabled:
            for tool in await self.mcp.connect_all():
                self.registry.register(tool)

    def build_auto_voice(self) -> Callable[[str, EmitFn], Awaitable[None]] | None:
        """Return the loop's auto-voice callback, or None when unavailable.

        Call after :meth:`startup` so MCP-provided voice tools are registered.
        """
        if not self.settings.auto_voice:
            return None
        tool = self.registry.get(self.settings.voice_tool)
        if tool is None:
            return None

        async def auto_voice(text: str, emit: EmitFn) -> None:
            # strip markdown so the TTS doesn't read out "星号/井号"
            clean = re.sub(r"[*_`#>\[\]]", "", text).strip()
            if not clean:
                return
            kwargs: dict = {"text": clean, "play": False}
            if self.settings.voice_name:
                kwargs["voice"] = self.settings.voice_name
            # the MCP tool's AUDIO: marker is forwarded via the bound emit channel
            async with request_context(emit):
                await tool.run(**kwargs)

        return auto_voice

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
            registry.register_all(
                WebSearchTool(
                    provider=settings.search_provider,
                    api_key=settings.search_api_key,
                    max_results=settings.search_max_results,
                ),
                WebFetchTool(allow_loopback=settings.network_allow_loopback),
            )
        if settings.enable_ecommerce:
            from agentmind.ecommerce.api import MockEcommerceAPI
            from agentmind.tools.ecommerce import (
                AfterSalesApplyTool,
                AfterSalesCheckTool,
                LogisticsTrackTool,
                OrderLookupTool,
            )

            self.ecommerce_api = MockEcommerceAPI()
            registry.register_all(
                OrderLookupTool(self.ecommerce_api),
                LogisticsTrackTool(self.ecommerce_api),
                AfterSalesCheckTool(self.ecommerce_api),
                AfterSalesApplyTool(self.ecommerce_api),
            )
        return registry

    def use_local_approvals(self) -> None:
        """Route approvals to a terminal prompt instead of the UI (CLI mode)."""
        self.gate.local_prompt = True

    async def shutdown(self) -> None:
        self.approvals.cancel_all()
        await self.mcp.close()
        await self.provider.close()
