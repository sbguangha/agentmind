"""Long-term memory consolidation (Dream-style).

Every turn is already stored as an ``episode`` memory. When episodes
accumulate past a batch threshold, the consolidator asks the LLM to fold the
oldest batch into a higher-level ``summary`` memory (preferences, facts,
decisions) and removes the raw episodes. Over time long-term memory stays
bounded and gets *better organized*, not just larger.
"""
from __future__ import annotations

from agentmind.config import Settings
from agentmind.memory.long_term import LongTermMemory
from agentmind.providers.base import LLMProvider

_CONSOLIDATE_SYSTEM_PROMPT = (
    "你是长期记忆整理器。下面是一批从多轮对话中抽取的记忆片段（episode）。"
    "请把它们归纳为 3-6 条简洁的长期记忆：保留稳定的用户偏好、事实、决定和重要上下文，"
    "去除重复与过时信息。直接输出归纳结果（每条一行，用 - 开头），不要解释。"
)


class MemoryConsolidator:
    def __init__(self, provider: LLMProvider, memory: LongTermMemory, settings: Settings) -> None:
        self._provider = provider
        self._memory = memory
        self._settings = settings

    async def maybe_consolidate(self) -> bool:
        """Consolidate the oldest episode batch if it reached the threshold."""
        if not self._settings.memory_consolidate:
            return False
        episodes = [e for e in await self._memory.all() if e.kind == "episode"]
        if len(episodes) < self._settings.consolidation_batch:
            return False

        chunk = episodes[: self._settings.consolidation_batch]
        summary = await self._summarize(chunk)
        if not summary:
            return False

        await self._memory.remember(summary, kind="summary")
        for entry in chunk:
            await self._memory.delete(entry.id)
        return True

    async def _summarize(self, episodes) -> str:
        text = "\n".join(e.content[:600] for e in episodes)[-16_000:]
        try:
            result = await self._provider.complete(
                [
                    {"role": "system", "content": _CONSOLIDATE_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                self._settings.model,
                temperature=0.3,
            )
        except Exception:  # noqa: BLE001 - consolidation is best-effort
            return ""
        return (result.content or "").strip()
