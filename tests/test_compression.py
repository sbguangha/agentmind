"""Tests for context compression and memory consolidation."""
from __future__ import annotations

from agentmind.core.compressor import Compressor
from agentmind.core.consolidator import MemoryConsolidator
from agentmind.memory.embeddings import Embedder
from agentmind.memory.long_term import LongTermMemory
from agentmind.memory.store import MemoryStore
from agentmind.session.types import Message, Session
from tests.conftest import make_settings
from tests.test_runner import MockProvider


# ---- compression -----------------------------------------------------
async def test_compressor_replaces_old_history(tmp_path):
    settings = make_settings(tmp_path, max_history_chars=10_000)
    provider = MockProvider(["压缩后的摘要文本"])
    session = Session(id="s")
    for i in range(20):
        session.messages.append(Message(role="user", content="这是一条很长的历史消息" * 100))

    compressor = Compressor(provider, settings)
    assert await compressor.maybe_compress(session) is True

    # a synthetic summary message exists and history shrank
    assert any(m.role == "system" and "摘要" in m.content for m in session.messages)
    assert len(session.messages) < 20
    assert session.last_compacted >= 1


async def test_compressor_skips_small_conversations(tmp_path):
    settings = make_settings(tmp_path)
    provider = MockProvider(["不应触发"])
    session = Session(id="s")
    session.messages.append(Message(role="user", content="短"))

    compressor = Compressor(provider, settings)
    assert await compressor.maybe_compress(session) is False
    assert len(session.messages) == 1


async def test_compressor_respects_min_messages(tmp_path):
    settings = make_settings(tmp_path, compression_min_messages=100)
    provider = MockProvider(["不应触发"])
    session = Session(id="s")
    for i in range(20):
        session.messages.append(Message(role="user", content="x" * 2000))

    compressor = Compressor(provider, settings)
    assert await compressor.maybe_compress(session) is False


# ---- consolidation ---------------------------------------------------
async def test_consolidator_batches_episodes(tmp_path):
    settings = make_settings(tmp_path, consolidation_batch=3)
    provider = MockProvider(["用户偏好简洁中文回答\n用户在使用 Windows 开发"])
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))

    for i in range(5):
        await memory.remember(f"episode 片段 {i}：用户说了第{i}件事", kind="episode")

    consolidator = MemoryConsolidator(provider, memory, settings)
    assert await consolidator.maybe_consolidate() is True

    entries = await memory.all()
    summaries = [e for e in entries if e.kind == "summary"]
    episodes = [e for e in entries if e.kind == "episode"]
    assert len(summaries) == 1
    assert len(episodes) == 2  # 5 - 3 batched


async def test_consolidator_idles_below_threshold(tmp_path):
    settings = make_settings(tmp_path, consolidation_batch=10)
    provider = MockProvider(["不触发"])
    memory = LongTermMemory(MemoryStore(tmp_path / "m.db"), Embedder(None, model=""))
    await memory.remember("只有一个", kind="episode")

    consolidator = MemoryConsolidator(provider, memory, settings)
    assert await consolidator.maybe_consolidate() is False
    assert len(await memory.all()) == 1
