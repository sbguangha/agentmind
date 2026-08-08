"""Long-term memory: semantic + keyword retrieval over the store.

The agent's long-term memory pipeline:
    * every finished turn is persisted as an *episode* (memory consolidation)
    * explicit ``remember`` tool calls persist *facts*
    * before each turn, memories similar to the user's query are recalled and
      injected into the system prompt as perception context

Retrieval strategy:
    * semantic  — cosine similarity over embeddings (when an embedding model
                  is configured)
    * keyword   — token overlap scoring, CJK-aware bigrams, works offline
"""
from __future__ import annotations

import math
import re
from datetime import datetime

from agentmind.memory.embeddings import Embedder
from agentmind.memory.store import MemoryEntry, MemoryStore


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _tokens(text: str) -> set[str]:
    """Tokenize for keyword scoring: latin words + CJK bigrams + CJK chars."""
    tokens: set[str] = set()
    text = text.lower()
    tokens.update(re.findall(r"[a-z0-9]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.update(cjk)
    for a, b in zip(cjk, cjk[1:]):
        tokens.add(a + b)
    return tokens


def _keyword_score(query_tokens: set[str], content: str) -> float:
    content_tokens = _tokens(content)
    if not content_tokens:
        return 0.0
    hits = query_tokens & content_tokens
    # weight: shared tokens relative to query size, bigrams count double
    score = sum(2 if len(t) > 1 else 1 for t in hits)
    return score / max(1, len(query_tokens))


class LongTermMemory:
    """Recalls and persists memories with graceful semantic->keyword fallback."""

    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    @property
    def semantic_enabled(self) -> bool:
        return self._embedder.enabled

    async def remember(self, content: str, kind: str = "episode") -> MemoryEntry:
        embedding = None
        if self._embedder.enabled:
            try:
                embedding = (await self._embedder.embed([content]))[0]
            except Exception:  # noqa: BLE001 - keep memory alive without vectors
                embedding = None
        return await self._store.add(content, kind=kind, embedding=embedding)

    async def recall(self, query: str, top_k: int = 4) -> list[MemoryEntry]:
        entries = await self._store.all()
        if not entries:
            return []

        query_vec = None
        if self._embedder.enabled:
            try:
                query_vec = (await self._embedder.embed([query]))[0]
            except Exception:  # noqa: BLE001
                query_vec = None

        if query_vec is not None:
            scored = [
                (e, cosine_similarity(query_vec, e.embedding or [])) for e in entries
            ]
        else:
            q_tokens = _tokens(query)
            scored = [(e, _keyword_score(q_tokens, e.content)) for e in entries]

        scored.sort(key=lambda item: item[1], reverse=True)
        results = [e for e, score in scored if score > 0][:top_k]
        return results

    async def recall_text(self, query: str, top_k: int = 4) -> str:
        """Recall formatted for direct injection into the system prompt."""
        hits = await self.recall(query, top_k)
        if not hits:
            return "（暂无相关记忆）"
        blocks = [
            f"- [{e.kind} {_fmt_time(e.created_at)}] {e.content[:300]}" for e in hits
        ]
        return "\n".join(blocks)

    async def all(self) -> list[MemoryEntry]:
        return await self._store.all()

    async def clear(self) -> int:
        return await self._store.clear()

    async def delete(self, entry_id: str) -> bool:
        """Remove a specific memory entry (used by consolidation)."""
        return await self._store.delete(entry_id)
