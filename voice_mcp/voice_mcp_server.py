"""Streaming TTS + local playback.

edge-tts synthesizes MP3 chunks incrementally (async). We write them to a
temporary file and start pygame playback as soon as enough audio is buffered,
so the first word is heard after only ~hundreds of milliseconds.

pygame reads the file lazily, so if playback hits a premature EOF while the
file is still growing it stops — a watchdog reloads and resumes playback. The
trade-off is a tiny overlap (a word or two) in rare slow-network cases.

Design notes for testability: the edge-tts communicator is injected via
``communicate_factory`` and the audio backend behind a tiny ``Playback``
protocol, so unit tests run with zero hardware and zero network.
"""
from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp.server.fastmcp import FastMCP

_START_THRESHOLD_BYTES = 4096  # ~0.5s of speech before playback begins
_PLAY_AGAIN_MIN_INTERVAL = 0.25  # avoid restart loops while the player loads
_PLAYBACK_TIMEOUT = 120.0  # hard cap so a stuck player can't block forever
_MAX_ATTEMPTS = 2  # edge-tts' Microsoft endpoint is throttled/unstable; retry once
_RETRY_DELAY = 0.8


class TTSFailure(RuntimeError):
    """Raised when synthesis or playback fails in a user-recoverable way."""


class AudioDeviceError(TTSFailure):
    """Raised when the local audio backend cannot initialize."""


class Playback(Protocol):
    """Minimal audio backend surface (real: pygame; tests: fake)."""

    def ensure_ready(self) -> None: ...
    def play(self, path: str) -> None: ...
    def stop(self) -> None: ...
    def is_playing(self) -> bool: ...


class PygamePlayback:
    """Real backend backed by pygame.mixer.music (imported lazily)."""

    _ready = False

    def ensure_ready(self) -> None:
        if PygamePlayback._ready:
            return
        try:
            # pygame prints a welcome banner to STDOUT on import, which would
            # corrupt the MCP JSON-RPC stream — suppress it (official flag).
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame

            pygame.mixer.init()
        except Exception as exc:  # noqa: BLE001 - surface any init failure
            raise AudioDeviceError(f"音频设备初始化失败（无输出设备?）: {exc}") from exc
        PygamePlayback._ready = True

    def play(self, path: str) -> None:
        import pygame

        pygame.mixer.music.load(path)
        pygame.mixer.music.play()

    def stop(self) -> None:
        import pygame

        pygame.mixer.music.stop()

    def is_playing(self) -> bool:
        import pygame

        try:
            return bool(pygame.mixer.music.get_busy())
        except pygame.error:
            return False


def _default_factory():
    from edge_tts import Communicate

    # edge-tts' endpoint (speech.platform.bing.com) is throttled and often
    # unreachable from CN networks — route through a local proxy if set.
    proxy = os.environ.get("EDGE_TTS_PROXY") or None

    def _make(text: str, voice: str):
        return Communicate(text, voice, proxy=proxy)

    return _make


@dataclass
class TTSPlayer:
    """Stream edge-tts audio to a temp file while playing it incrementally."""

    communicate_factory: Any = field(default_factory=_default_factory)
    playback: Playback = field(default_factory=PygamePlayback)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def speak(self, text: str, voice: str) -> bytes:
        """Stream + local playback; returns the full MP3 bytes for other clients."""
        self.playback.ensure_ready()
        async with self._lock:  # serialize playback; a new call preempts the old one
            last_exc: TTSFailure | None = None
            for attempt in range(_MAX_ATTEMPTS):
                self.playback.stop()
                try:
                    return await self._stream_and_play(text, voice)
                except AudioDeviceError:
                    raise
                except TTSFailure as exc:  # network is flaky — retry once
                    last_exc = exc
                    if attempt + 1 < _MAX_ATTEMPTS:
                        await asyncio.sleep(_RETRY_DELAY)
            raise last_exc  # type: ignore[misc]

    async def synthesize(self, text: str, voice: str) -> bytes:
        """Stream and return the full MP3 bytes without local playback."""
        comm = self.communicate_factory(text, voice)
        buf = bytearray()
        try:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buf += chunk["data"]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSFailure(f"{type(exc).__name__}: {exc}") from exc
        return bytes(buf)

    # ------------------------------------------------------------------
    async def _stream_and_play(self, text: str, voice: str) -> bytes:
        comm = self.communicate_factory(text, voice)
        fd, path = tempfile.mkstemp(suffix=".mp3")
        write_done = asyncio.Event()
        watchdog = asyncio.create_task(self._watchdog(path, write_done))
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in comm.stream():
                    if chunk.get("type") != "audio":
                        continue
                    data = chunk.get("data")
                    if not data:
                        continue
                    fh.write(data)
                    fh.flush()
                    if fh.tell() >= _START_THRESHOLD_BYTES:
                        self._safe_play(path)
            write_done.set()
            await watchdog
            with open(path, "rb") as rf:
                return rf.read()
        except asyncio.CancelledError:
            await self._stop_watchdog(watchdog, write_done)
            self.playback.stop()
            raise
        except Exception as exc:  # noqa: BLE001 - wrap into TTSFailure
            await self._stop_watchdog(watchdog, write_done)
            self.playback.stop()
            raise TTSFailure(f"{type(exc).__name__}: {exc}") from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    async def _stop_watchdog(watchdog: asyncio.Task, write_done: asyncio.Event) -> None:
        """Terminate the watchdog on failure/cancellation paths."""
        write_done.set()
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog

    def _safe_play(self, path: str) -> None:
        try:
            self.playback.play(path)
        except Exception:  # noqa: BLE001 - keep trying; watchdog retries
            pass

    # ------------------------------------------------------------------
    async def _watchdog(self, path: str, write_done: asyncio.Event) -> None:
        """Resume playback when pygame hits a premature EOF mid-write, then
        wait for playback to finish once writing completes."""
        last_play = 0.0

        while True:
            if (
                not self.playback.is_playing()
                and os.path.getsize(path) >= _START_THRESHOLD_BYTES
                and time.monotonic() - last_play >= _PLAY_AGAIN_MIN_INTERVAL
            ):
                self._safe_play(path)
                last_play = time.monotonic()
            try:
                await asyncio.wait_for(write_done.wait(), timeout=0.1)
                break
            except asyncio.TimeoutError:
                continue

        # writing finished: play the complete file if it never started
        if not self.playback.is_playing() and os.path.getsize(path) > 0:
            self._safe_play(path)

        deadline = time.monotonic() + _PLAYBACK_TIMEOUT
        while self.playback.is_playing() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)


# ===========================================================================
# MCP server
# ===========================================================================

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
MAX_TEXT_CHARS = 1000

# Curated set of commonly used voices (edge-tts supports many more).
VALID_VOICES = frozenset({
    # Chinese (Mandarin)
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural", "zh-CN-YunxiaNeural", "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural",
    # Chinese (Cantonese / Traditional)
    "zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural",
    "zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural",
    # English
    "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-EmmaNeural",
    "en-GB-SoniaNeural", "en-GB-RyanNeural",
    # Japanese / Korean
    "ja-JP-NanamiNeural", "ja-JP-KeitaNeural",
    "ko-KR-SunHiNeural", "ko-KR-InJoonNeural",
})

VOICE_ALIASES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "yunxi": "zh-CN-YunxiNeural",
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "yunjian": "zh-CN-YunjianNeural",
    "yunyang": "zh-CN-YunyangNeural",
    "xiaobei": "zh-CN-liaoning-XiaobeiNeural",
    "en": "en-US-AriaNeural",
    "english": "en-US-AriaNeural",
    "japanese": "ja-JP-NanamiNeural",
    "korean": "ko-KR-SunHiNeural",
}

mcp = FastMCP("voice-speak")
_player = TTSPlayer()


def _resolve_voice(voice: str) -> str | None:
    key = voice.strip()
    if not key:
        return DEFAULT_VOICE
    if key in VALID_VOICES:
        return key
    alias = VOICE_ALIASES.get(key.lower())
    return alias if alias else None


@mcp.tool()
async def voice_speak(text: str, voice: str = DEFAULT_VOICE, play: bool = True) -> str:
    """将文本合成为语音并在本机播放（edge-tts 流式合成 + pygame 边生成边播放）。

    - text: 需要朗读的文本（最长 1000 字符，超出自动截断）
    - voice: 音色名或别名，默认 zh-CN-XiaoxiaoNeural；
      常用别名：xiaoxiao / yunxi / yunjian / xiaobei / en / japanese / korean
    - play: 是否在本机扬声器播放（默认 true）；false 时只合成，供客户端拿到音频自行播放
    返回播报状态，并在末尾附带一行 AUDIO:<mime>:<base64>，携带完整音频供客户端播放。
    """
    if not text or not text.strip():
        return "错误: text 参数不能为空。"
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS] + "……"

    voice = _resolve_voice(voice or DEFAULT_VOICE)
    if voice is None:
        sample = ", ".join(sorted(VALID_VOICES)[:8])
        return f"错误: 未知音色。可选: {sample} …（也支持别名 xiaoxiao/yunxi/en/japanese）"

    try:
        if play:
            audio = await _player.speak(text, voice)
            status = f"语音已播报（{voice}）："
        else:
            audio = await _player.synthesize(text, voice)
            status = f"已生成语音（{voice}）："
    except AudioDeviceError as exc:
        return f"错误: 本机音频不可用: {exc}"
    except TTSFailure as exc:
        return f"错误: 语音合成失败: {exc}"
    except Exception as exc:  # noqa: BLE001 - never crash the MCP request
        return f"错误: 未知异常: {type(exc).__name__}: {exc}"

    preview = text if len(text) <= 40 else text[:40] + "…"
    if truncated:
        preview += "（内容过长已截断）"
    b64 = base64.b64encode(audio).decode("ascii")
    return f"{status}{preview}\nAUDIO:audio/mpeg:{b64}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
