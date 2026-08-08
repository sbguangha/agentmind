"""Session message types — the short-term memory data model."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from agentmind.providers.base import ToolCall


@dataclass
class Message:
    """One message in a session. Serializable to OpenAI API format."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None  # tool name for "tool" role
    tool_call_id: str | None = None  # for "tool" role replies
    tool_calls: list[ToolCall] | None = None  # for "assistant" role
    timestamp: float = field(default_factory=time.time)

    def to_api(self) -> dict:
        """Convert to an OpenAI chat-completion message."""
        msg: dict = {"role": self.role, "content": self.content}
        if self.name:
            msg["name"] = self.name
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type or "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        return msg

    def to_dict(self) -> dict:
        data: dict = {"role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.name:
            data["name"] = self.name
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            timestamp=data.get("timestamp", 0.0),
        )


@dataclass
class Session:
    """A conversation with its own history (short-term memory)."""

    id: str
    title: str = "新对话"
    messages: list[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_compacted: int = 0  # messages[:last_compacted] were already summarized

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_compacted": self.last_compacted,
        }

    def context_window(self, max_chars: int) -> list["Message"]:
        """Return the tail of history fitting inside the memory budget."""
        budget = max_chars
        window: list[Message] = []
        for msg in reversed(self.messages):
            budget -= len(msg.content)
            if budget < 0:
                break
            window.append(msg)
        return list(reversed(window))

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(
            id=data["id"],
            title=data.get("title", "新对话"),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            last_compacted=data.get("last_compacted", 0),
        )

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]
