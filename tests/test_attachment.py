"""Tests for voice attachment persistence: audio saved to disk, a UI-only voice
message stored in the session so the bubble survives reloads / session switches."""
from __future__ import annotations

import asyncio
import base64

from agentmind.api.server import AgentServer
from agentmind.bus.queue import MessageBus, OutboundMessage
from agentmind.config import Settings
from agentmind.core.loop import AgentLoop
from agentmind.runtime import AgentRuntime
from agentmind.session.types import Message


def test_message_attachment_roundtrip_and_toapi_skip():
    msg = Message(role="assistant", content="", attachment={"kind": "voice", "url": "/api/audio/x.mp3", "text": "语音"})
    restored = Message.from_dict(msg.to_dict())
    assert restored.attachment == {"kind": "voice", "url": "/api/audio/x.mp3", "text": "语音"}
    # UI-only attachment messages must not leak into the model API format
    assert "attachment" not in msg.to_api()


async def test_audio_attachment_persisted(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path / "data"),
        workspace=str(tmp_path / "workspace"),
        memory_auto_store=False,
        enable_web=False,
    )
    runtime = AgentRuntime(settings)
    bus = MessageBus()
    loop = AgentLoop(bus, runtime.runner, runtime.sessions, runtime.memory, settings)
    server = AgentServer(runtime, bus, loop)
    await server.start("127.0.0.1", 0)
    session = runtime.sessions.create()
    audio_bytes = b"\x00\x01\x02MOCKMP3"
    try:
        await bus.publish_outbound(
            OutboundMessage(
                session_id=session.id,
                event="attachment",
                payload={
                    "mime": "audio/mpeg",
                    "data": base64.b64encode(audio_bytes).decode(),
                    "label": "语音已播报（zh-CN-XiaoxiaoNeural）：你好",
                },
            )
        )
        # wait for the fanout to persist it
        voice = []
        for _ in range(100):
            await asyncio.sleep(0.05)
            reloaded = runtime.sessions.get(session.id)
            voice = [m for m in reloaded.messages if m.attachment]
            if voice:
                break
        assert voice, "voice message should be persisted into the session"
        url = voice[0].attachment["url"]
        assert url.startswith("/api/audio/")

        name = url.rsplit("/", 1)[1]
        audio_path = runtime.settings.resolved_data_dir / "audio" / name
        assert audio_path.is_file()
        assert audio_path.read_bytes() == audio_bytes

        # the message survives a full reload from disk
        manager2 = __import__("agentmind.session.manager", fromlist=["SessionManager"]).SessionManager(
            settings.resolved_data_dir
        )
        reloaded2 = manager2.get(session.id)
        assert reloaded2 and any(m.attachment for m in reloaded2.messages)
    finally:
        await server.shutdown()
        await runtime.shutdown()
