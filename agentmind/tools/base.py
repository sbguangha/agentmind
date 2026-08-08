"""Tool abstraction.

A tool is the agent's *hand*: a typed, documented capability the model can
invoke. Tools are surfaced to the model as OpenAI function schemas, so the
model decides *when* and *with what arguments* to act — that decision loop
is the ReAct core.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """Result of executing a tool."""

    output: str  # text fed back to the model (and shown in the UI)
    data: dict | None = None  # optional structured data for the UI
    is_error: bool = False


class Tool(ABC):
    """Base class every tool must implement."""

    name: str = ""
    description: str = ""
    # JSON Schema for the arguments object (see tools/openapi style)
    parameters: dict = field(default_factory=dict)

    def schema(self) -> dict:
        """OpenAI function-calling schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        """Execute the tool with validated keyword arguments."""
        raise NotImplementedError
