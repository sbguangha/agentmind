"""Tests for the voice_speak MCP server (mocked edge-tts stream + mocked playback)."""
from __future__ import annotations

import time

import pytest

import voice_mcp_server
from voice_mcp_server import (
    AudioDeviceError,
    MAX_TEXT_CHARS,
    TTSFailure,
    TTSPlayer,
    _resolve_voice,
    voice_speak,
)


class FakeCommunicator:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def stream(self):
        self.calls.append("stream")
        for chunk in self.chunks:
            yield chunk


class FakePlayback:
    """Emulates a player that plays each file for ~40ms then stops."""

    def __init__(self, *, fail_init=False):
        self.fail_init = fail_init
        self.played: list[str] = []
        self.stop_calls = 0
        self._until = 0.0

    def ensure_ready(self):
        if self.fail_init:
            raise AudioDeviceError("no audio device in test")

    def play(self, path):
        self.played.append(path)
        self._until = time.monotonic() + 0.04

    def stop(self):
        self.stop_calls += 1
        self._until = 0.0

    def is_playing(self):
        return time.monotonic() < self._until


def make_player(chunks, playback=None):
    playback = playback or FakePlayback()
    return TTSPlayer(
        communicate_factory=lambda text, voice: FakeCommunicator(chunks),
        playback=playback,
    ), playback


def audio_chunks(n=20, size=1024):
    return [{"type": "audio", "data": b"x" * size} for _ in range(n)]


# ---- TTSPlayer ---------------------------------------------------------
async def test_streams_and_plays():
    player, playback = make_player(audio_chunks(n=20, size=1024))  # 20KB > threshold
    await player.speak("这是一段比较长的测试文本", "zh-CN-XiaoxiaoNeural")
    assert playback.played, "playback should have started"
    assert len(set(playback.played)) == 1, "should play the same temp file"


async def test_short_text_plays_complete_file():
    player, playback = make_player(audio_chunks(n=1, size=512))  # below threshold
    await player.speak("短", "zh-CN-XiaoxiaoNeural")
    assert playback.played, "complete short file must still be played"


async def test_synthesis_failure_wrapped():
    class Boom(FakeCommunicator):
        async def stream(self):
            yield {"type": "audio", "data": b"x"}
            raise RuntimeError("edge server 500")

    player = TTSPlayer(
        communicate_factory=lambda text, voice: Boom([]),
        playback=FakePlayback(),
    )
    with pytest.raises(TTSFailure, match="edge server 500"):
        await player.speak("你好", "zh-CN-XiaoxiaoNeural")


async def test_retries_flaky_network():
    """First attempt fails (throttled endpoint), second attempt succeeds."""
    state = {"attempts": 0}

    class Flaky(FakeCommunicator):
        async def stream(self):
            state["attempts"] += 1
            if state["attempts"] == 1:
                raise RuntimeError("Connection timeout to host speech.platform.bing.com")
            for chunk in self.chunks:
                yield chunk

    playback = FakePlayback()
    player = TTSPlayer(
        communicate_factory=lambda text, voice: Flaky(audio_chunks(n=8)),
        playback=playback,
    )
    await player.speak("你好", "zh-CN-XiaoxiaoNeural")
    assert state["attempts"] == 2
    assert playback.played


async def test_audio_device_failure_surfaces():
    player = TTSPlayer(
        communicate_factory=lambda text, voice: FakeCommunicator(audio_chunks()),
        playback=FakePlayback(fail_init=True),
    )
    with pytest.raises(AudioDeviceError):
        await player.speak("你好", "zh-CN-XiaoxiaoNeural")


# ---- server tool -------------------------------------------------------
async def test_tool_empty_text(monkeypatch):
    player, _ = make_player([])
    monkeypatch.setattr(voice_mcp_server, "_player", player)
    assert "不能为空" in await voice_speak("   ")


async def test_tool_invalid_voice(monkeypatch):
    player, _ = make_player([])
    monkeypatch.setattr(voice_mcp_server, "_player", player)
    result = await voice_speak("你好", voice="not-a-voice")
    assert "未知音色" in result


async def test_tool_ok(monkeypatch):
    player, playback = make_player(audio_chunks())
    monkeypatch.setattr(voice_mcp_server, "_player", player)
    result = await voice_speak("你好，这是测试", voice="xiaoxiao")
    assert result.startswith("语音已播报")
    assert "zh-CN-XiaoxiaoNeural" in result
    assert playback.played


async def test_tool_audio_device_error(monkeypatch):
    player = TTSPlayer(
        communicate_factory=lambda text, voice: FakeCommunicator(audio_chunks()),
        playback=FakePlayback(fail_init=True),
    )
    monkeypatch.setattr(voice_mcp_server, "_player", player)
    assert "音频不可用" in await voice_speak("你好")


async def test_tool_truncates_long_text(monkeypatch):
    player, _ = make_player([])
    monkeypatch.setattr(voice_mcp_server, "_player", player)
    long_text = "长" * (MAX_TEXT_CHARS + 100)
    result = await voice_speak(long_text)
    assert "截断" in result


# ---- voice resolution --------------------------------------------------
def test_voice_resolution():
    assert _resolve_voice("") == "zh-CN-XiaoxiaoNeural"
    assert _resolve_voice("zh-CN-YunxiNeural") == "zh-CN-YunxiNeural"
    assert _resolve_voice("yunxi") == "zh-CN-YunxiNeural"
    assert _resolve_voice("en") == "en-US-AriaNeural"
    assert _resolve_voice("bogus") is None
