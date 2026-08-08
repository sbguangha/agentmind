"""Embedding abstraction for semantic retrieval.

If the configured provider exposes an embedding model we get real vector
recall; otherwise retrieval falls back to keyword scoring (see
``long_term.py``). Either way the agent keeps its memory working.
"""
from __future__ import annotations

from agentmind.providers.base import LLMProvider, ProviderError


class Embedder:
    """Thin wrapper over a provider's embedding endpoint."""

    def __init__(self, provider: LLMProvider, model: str = "") -> None:
        self._provider = provider
        self.model = model

    @property
    def enabled(self) -> bool:
        return bool(self.model)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled:
            raise ProviderError("Embedding model not configured")
        return await self._provider.embed(texts, self.model)
