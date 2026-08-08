"""Shared test fixtures."""
from __future__ import annotations

import pytest

from agentmind.config import Settings
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.types import Session
from agentmind.tools.base import Tool, ToolResult


def make_settings(tmp_path, **overrides) -> Settings:
    base = {
        "data_dir": str(tmp_path / "data"),
        "workspace": str(tmp_path / "workspace"),
        "memory_auto_store": False,
    }
    base.update(overrides)
    return Settings(**base)


class DeterministicEmbedder(Embedder):
    """Hash-based embedder: similar strings get similar vectors."""

    def __init__(self, model: str = "test") -> None:
        self.model = model
        self.enabled_flag = True

    @property
    def enabled(self) -> bool:
        return self.enabled_flag

    async def embed(self, texts: list[str]) -> list[list[float]]:
        def vec(text: str) -> list[float]:
            buckets = [0.0] * 16
            for ch in text:
                buckets[ord(ch) % 16] += 1.0
            total = sum(buckets) or 1.0
            return [b / total for b in buckets]

        return [vec(t) for t in texts]


class NullProvider:
    """Stand-in provider used where embedding is all that is needed."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    lm = LongTermMemory(store, DeterministicEmbedder())
    return lm


@pytest.fixture
def session():
    return Session(id="sess-test", title="测试")


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


class EchoTool(Tool):
    name = "echo"
    description = "echo back the text"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, text: str = "", **kwargs) -> ToolResult:
        return ToolResult(output=f"echo:{text}")
