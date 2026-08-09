"""Command line entry points.

    agentmind                 start the WebUI + gateway server
    agentmind chat            interactive terminal chat
    agentmind --version       show version
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from agentmind.bus.queue import MessageBus
from agentmind.config import Settings
from agentmind.core.loop import AgentLoop
from agentmind.runtime import AgentRuntime
from agentmind.session.types import Message, Session


def main() -> None:
    # Windows console uses GBK by default; model output may contain chars it
    # cannot encode (e.g. emoji). Replace instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="agentmind", description="AgentMind - a complete AI agent")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "chat"])
    parser.add_argument("--host", default=None, help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="bind port (default 8765)")
    parser.add_argument("--config", default=None, help="path to a JSON config file")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args()

    if args.version:
        from agentmind import __version__

        print(f"AgentMind {__version__}")
        return

    settings = Settings.load(Path(args.config) if args.config else None)
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    if args.command == "serve":
        _run_serve(settings)
    else:
        _run_chat(settings)


# ----------------------------------------------------------------------
def _run_serve(settings: Settings) -> None:
    async def entry() -> None:
        from agentmind.api.server import AgentServer

        runtime = AgentRuntime(settings)
        await runtime.startup()
        bus = MessageBus()
        loop = AgentLoop(
            bus,
            runtime.runner,
            runtime.sessions,
            runtime.memory,
            settings,
            compressor=runtime.compressor,
            consolidator=runtime.consolidator,
            scope_resolver=runtime.scope_resolver,
            auto_voice=runtime.build_auto_voice(),
        )

        server = AgentServer(runtime, bus, loop)
        await server.start(settings.host, settings.port)
        loop_task = asyncio.create_task(loop.run())

        stop = asyncio.Event()

        def _on_signal() -> None:
            stop.set()

        loop_ = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop_.add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                pass

        try:
            await stop.wait()
        finally:
            loop.stop()
            loop_task.cancel()
            await server.shutdown()
            await runtime.shutdown()

    try:
        asyncio.run(entry())
    except KeyboardInterrupt:
        pass


def _run_chat(settings: Settings) -> None:
    """Minimal interactive terminal chat (nice for quick demos)."""

    async def entry() -> None:
        runtime = AgentRuntime(settings)
        await runtime.startup()
        runtime.use_local_approvals()
        session: Session = runtime.sessions.create()

        def emit_direct(event: str, payload: dict) -> None:
            if event == "delta":
                print(payload["text"], end="", flush=True)
            elif event == "tool_start":
                print(f"\n[工具] {payload['name']}({payload['arguments']}) ...", flush=True)
            elif event == "tool_end":
                print("完成", flush=True)
            elif event == "thinking_start":
                print("\n[思考]", end=" ", flush=True)

        print(f"AgentMind CLI（模型: {settings.model}，输入 exit 退出）")
        while True:
            try:
                text = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in {"exit", "quit"}:
                break

            async def emit(event: str, payload: dict) -> None:
                emit_direct(event, payload)

            try:
                answer = await runtime.runner.run_turn(session, text, emit)
                print(f"\n助手> {answer}", flush=True)
                await runtime.sessions.append(session, Message(role="user", content=text))
                await runtime.sessions.append(session, Message(role="assistant", content=answer))
                if settings.memory_auto_store and answer.strip():
                    await runtime.memory.remember(f"用户问: {text[:200]}\n助手答: {answer[:400]}", kind="episode")
            except Exception as exc:  # noqa: BLE001
                print(f"\n[错误] {exc}", file=sys.stderr)

        await runtime.shutdown()

    try:
        asyncio.run(entry())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
