"""Provider abstractions.

A ``LLMProvider`` is the model-facing half of the perception layer: it turns
context into tokens and returns structured results (text or tool calls).
Only two capabilities are required of every provider:
    * streaming chat completions with tool calling
    * text embeddings (optional, used by long-term memory)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


class ProviderError(RuntimeError):
    """Raised when the model backend rejects or fails a request."""


@dataclass
class ToolCall:
    """A fully assembled tool invocation requested by the model."""

    id: str
    name: str
    arguments: str  # JSON string
    type: str = "function"


@dataclass
class ToolCallDelta:
    """An incremental fragment of a tool call received from a stream."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""
    type: str | None = None


@dataclass
class StreamChunk:
    """One chunk from an SSE chat stream."""

    content_delta: str | None = None
    tool_calls: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass
class ChatResult:
    """A completed (non-streamed) chat completion."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class LLMProvider(ABC):
    """Interface implemented by every model backend."""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion, yielding incremental chunks."""

    @abstractmethod
    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Embed a list of texts into vectors. ``model`` may be empty string."""
