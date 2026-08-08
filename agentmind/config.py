"""Application configuration.

Settings are resolved in this order (later wins):
    1. built-in defaults
    2. a JSON file at ``data_dir/config.json`` (if present)
    3. environment variables prefixed with ``AGENTMIND_``
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Runtime settings for the agent."""

    # ---- LLM / provider -------------------------------------------------
    api_base: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI-compatible base URL (OpenAI / DeepSeek / Ollama / vLLM ...)",
    )
    api_key: str = Field(default="", description="API key. Leave empty for local servers.")
    model: str = Field(default="gpt-4o-mini", description="Chat model id")
    embedding_model: str = Field(
        default="",
        description="Embedding model id. Empty string disables semantic (vector) recall "
        "and falls back to keyword-based retrieval.",
    )
    temperature: float = 0.7

    # ---- ReAct loop ------------------------------------------------------
    max_tool_rounds: int = Field(default=6, description="Max reason-act rounds per user turn")
    max_history_chars: int = Field(default=20000, description="Short-term memory budget per context")

    # ---- Memory ----------------------------------------------------------
    memory_top_k: int = Field(default=4, description="Long-term memories recalled per turn")
    memory_auto_store: bool = Field(
        default=True, description="Automatically persist each finished turn into long-term memory"
    )

    # ---- Tool security ---------------------------------------------------
    allow_shell: bool = Field(default=False, description="Enable the shell tool (runs commands)")
    enable_web: bool = Field(default=True, description="Enable web search / fetch tools")
    workspace: str = Field(default="./workspace", description="Filesystem tools are confined to this dir")

    # ---- Search (web_search) ---------------------------------------------
    search_provider: str = Field(
        default="bing",
        description="Search engine: bing (no key, works in CN) | duckduckgo | bocha | "
        "volcengine | tavily | brave | serper | jina",
    )
    search_api_key: str = Field(
        default="", description="API key for key-based search providers (env AGENTMIND_SEARCH_API_KEY)"
    )
    search_max_results: int = Field(default=5, description="Results returned per search")

    # ---- Workspace access (permission scope) -----------------------------
    workspace_access: str = Field(
        default="restricted",
        description="'restricted' 工具只能访问工作区 | 'full' 可访问整个文件系统（危险）",
    )

    # ---- Network security (SSRF) -----------------------------------------
    network_allow_loopback: bool = Field(
        default=False,
        description="Allow web_fetch to localhost/private addresses (SSRF risk)",
    )

    # ---- Web access control ----------------------------------------------
    web_token: str = Field(
        default="", description="If set, the WebUI/API requires ?token=<this> (nanobot-style gate)"
    )

    # ---- Human-in-the-loop approval --------------------------------------
    approval_mode: str = Field(
        default="ask_risky",
        description="'auto' 全部自动执行 | 'ask_risky' 危险工具需人工审批 | 'ask_all' 所有工具需审批",
    )
    approval_timeout: float = Field(default=120.0, description="审批等待超时(秒)，超时按拒绝处理")

    # ---- Subagent delegation ---------------------------------------------
    enable_subagents: bool = Field(default=True, description="Enable the delegate tool")
    max_subagent_depth: int = Field(default=2, description="Max nested subagent depth")
    max_concurrent_subagents: int = Field(default=4, description="Max concurrent subagent runs")

    # ---- Context compression & memory consolidation ----------------------
    context_compress: bool = Field(
        default=True,
        description="When short-term history exceeds budget, LLM-compress the old part "
        "instead of dropping it",
    )
    compression_min_messages: int = Field(default=8, description="Only compress conversations longer than this")
    memory_consolidate: bool = Field(
        default=True, description="Periodically batch-summarize episodes into higher-level memories"
    )
    consolidation_batch: int = Field(default=8, description="Episodes to batch per consolidation")

    # ---- Server ----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: str = Field(default="./data", description="Sessions + memory persistence")
    session_ttl_days: int = 30

    system_prompt: str = Field(default="", description="Custom system prompt (optional)")

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def resolved_workspace(self) -> Path:
        return Path(self.workspace).expanduser().resolve()

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        """Load settings: defaults -> JSON file -> environment variables."""
        values: dict[str, Any] = {}

        json_path = config_path or cls._default_config_path()
        if json_path and json_path.exists():
            values.update(json.loads(json_path.read_text(encoding="utf-8")))

        env_map = {
            "AGENTMIND_API_BASE": "api_base",
            "AGENTMIND_API_KEY": "api_key",
            "AGENTMIND_MODEL": "model",
            "AGENTMIND_EMBEDDING_MODEL": "embedding_model",
            "AGENTMIND_DATA_DIR": "data_dir",
            "AGENTMIND_WORKSPACE": "workspace",
            "AGENTMIND_HOST": "host",
            "AGENTMIND_PORT": "port",
            "AGENTMIND_ALLOW_SHELL": "allow_shell",
            "AGENTMIND_ENABLE_WEB": "enable_web",
            "AGENTMIND_SEARCH_API_KEY": "search_api_key",
            "AGENTMIND_SEARCH_PROVIDER": "search_provider",
            "AGENTMIND_WORKSPACE_ACCESS": "workspace_access",
            "AGENTMIND_WEB_TOKEN": "web_token",
        }
        for env_name, field_name in env_map.items():
            raw = os.environ.get(env_name)
            if raw is not None and raw != "":
                values[field_name] = _coerce(raw)

        return cls(**values)

    @staticmethod
    def _default_config_path() -> Path | None:
        default_data = Path("./data").expanduser()
        candidate = default_data / "config.json"
        return candidate if candidate.exists() else None


def _coerce(raw: str) -> Any:
    low = raw.strip().lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        return raw
