"""Context compression for short-term memory.

Previously, history that overflowed the budget was simply *dropped*
(``Session.context_window``). Compression instead asks the LLM to summarize
the oldest un-compacted part of the conversation and replaces it with a
single synthetic summary message — so nothing is lost, just densified.

This mirrors the consolidation side of nanobot's memory system (old messages
archived into a summary that is injected on the next turn).
"""
from __future__ import annotations

from agentmind.config import Settings
from agentmind.providers.base import LLMProvider
from agentmind.session.types import Message, Session

_SUMMARY_SYSTEM_PROMPT = (
    "你是对话压缩器。把下面的历史对话压缩成一段紧凑的中文摘要，必须保留："
    "1) 用户的偏好与身份信息；2) 已做出的决定与结论；3) 关键事实与数据；"
    "4) 尚未完成的任务。直接用摘要作答，不要任何解释或客套。"
)


class Compressor:
    def __init__(self, provider: LLMProvider, settings: Settings) -> None:
        self._provider = provider
        self._settings = settings

    async def maybe_compress(self, session: Session) -> bool:
        """Compress old history when it exceeds the budget. Returns True if compressed."""
        if not self._settings.context_compress:
            return False
        if len(session.messages) < self._settings.compression_min_messages:
            return False

        budget = self._settings.max_history_chars
        total = sum(len(m.content) for m in session.messages)
        if total <= budget:
            return False

        boundary = self._find_boundary(session, budget)
        if boundary <= session.last_compacted:
            return False

        chunk = session.messages[session.last_compacted:boundary]
        summary = await self._summarize(chunk)
        if not summary:
            return False

        # Replace the compressed chunk with a single synthetic summary message.
        del session.messages[session.last_compacted:boundary]
        session.messages.insert(
            session.last_compacted,
            Message(role="system", content=f"【历史对话摘要】\n{summary}"),
        )
        session.last_compacted = session.last_compacted + 1
        return True

    def _find_boundary(self, session: Session, budget: int) -> int:
        """Return the index of the first message to *keep* (older ones compress)."""
        remaining = budget
        keep_from = len(session.messages)
        for msg in reversed(session.messages):
            if remaining - len(msg.content) < 0:
                break
            remaining -= len(msg.content)
            keep_from -= 1
        return max(keep_from, session.last_compacted + 1)

    async def _summarize(self, chunk: list[Message]) -> str:
        text = "\n".join(f"[{m.role}] {m.content}" for m in chunk)[-12_000:]
        try:
            result = await self._provider.complete(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                self._settings.model,
                temperature=0.3,
            )
        except Exception:  # noqa: BLE001 - compression is best-effort
            return ""
        return (result.content or "").strip()
