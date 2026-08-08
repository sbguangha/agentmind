"""Tests for the long-term memory layer."""
from __future__ import annotations

from agentmind.memory.long_term import _tokens, cosine_similarity


async def test_remember_and_recall_semantic(memory):
    await memory.remember("用户喜欢喝美式咖啡", kind="fact")
    await memory.remember("项目部署在华为云服务器上", kind="fact")

    hits = await memory.recall("咖啡", top_k=1)
    assert len(hits) == 1
    assert "咖啡" in hits[0].content


async def test_recall_returns_nothing_when_empty(memory):
    assert await memory.recall("anything") == []
    assert "暂无相关记忆" in await memory.recall_text("anything")


async def test_recall_text_format(memory):
    await memory.remember("今天天气很好", kind="episode")
    text = await memory.recall_text("天气")
    assert "天气很好" in text


async def test_recall_text_without_embedding(tmp_path):
    from agentmind.memory.embeddings import Embedder
    from agentmind.memory.long_term import LongTermMemory
    from agentmind.memory.store import MemoryStore

    class NoopEmbedder(Embedder):
        @property
        def enabled(self) -> bool:
            return False

    lm = LongTermMemory(MemoryStore(tmp_path / "m.db"), NoopEmbedder(None, model=""))
    await lm.remember("AgentMind 支持文件读写与网络搜索", kind="fact")
    await lm.remember("用户住在上海", kind="fact")

    hits = await lm.recall("网络搜索", top_k=3)
    assert hits and "网络搜索" in hits[0].content


def test_cosine_similarity():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    assert cosine_similarity(a, b) == 1.0
    assert cosine_similarity(a, c) == 0.0


def test_tokens_cjk_bigrams():
    toks = _tokens("我喜欢Python和JavaScript")
    assert "python" in toks
    assert "javascript" in toks
    assert "我喜" in toks  # CJK bigram


async def test_clear(memory):
    await memory.remember("a", kind="fact")
    assert await memory.clear() == 1
    assert await memory.recall("a") == []
