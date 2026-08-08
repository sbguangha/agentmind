"""Web server: WebSocket chat, REST APIs and an OpenAI-compatible endpoint."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from agentmind.bus.queue import InboundMessage, MessageBus
from agentmind.core.loop import AgentLoop
from agentmind.runtime import AgentRuntime

_WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"


class AgentServer:
    """aiohttp application hosting the chat WebSocket + REST APIs."""

    def __init__(self, runtime: AgentRuntime, bus: MessageBus, loop: AgentLoop) -> None:
        self._runtime = runtime
        self._bus = bus
        self._loop = loop
        self._clients: dict[str, web.WebSocketResponse] = {}
        self._runner: web.AppRunner | None = None
        self._fanout_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    async def start(self, host: str, port: int) -> int:
        app = web.Application(middlewares=[_cors_middleware])
        self._register_routes(app)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        actual_port = self._runner.addresses[0][1]
        self._fanout_task = asyncio.create_task(self._fanout())
        print(f"AgentMind WebUI: http://{host}:{actual_port}")
        return actual_port

    async def shutdown(self) -> None:
        if self._fanout_task:
            self._fanout_task.cancel()
        for ws in list(self._clients.values()):
            if not ws.closed:
                await ws.close()
        if self._runner:
            await self._runner.cleanup()

    # ---- routing -------------------------------------------------------
    def _register_routes(self, app: web.Application) -> None:
        app.router.add_get("/", self._index)
        app.router.add_static("/static/", _WEBUI_DIR, name="static")
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_get("/api/sessions", self._list_sessions)
        app.router.add_post("/api/sessions", self._create_session)
        app.router.add_get("/api/sessions/{sid}/messages", self._session_messages)
        app.router.add_delete("/api/sessions/{sid}", self._delete_session)
        app.router.add_get("/api/memory", self._list_memory)
        app.router.add_delete("/api/memory", self._clear_memory)
        app.router.add_post("/v1/chat/completions", self._openai_completions)

    async def _index(self, request: web.Request) -> web.Response:
        return web.FileResponse(_WEBUI_DIR / "index.html")

    # ---- outbound fan-out ---------------------------------------------
    async def _fanout(self) -> None:
        while True:
            msg = await self._bus.receive_outbound()
            ws = self._clients.get(msg.session_id)
            if ws is not None and not ws.closed:
                await ws.send_json({"event": msg.event, "payload": msg.payload})

    # ---- WebSocket -----------------------------------------------------
    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=1 << 20)
        await ws.prepare(request)

        session_id: str | None = None
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw.data if isinstance(raw.data, str) else raw.data.decode())
                except (json.JSONDecodeError, AttributeError):
                    continue

                if data.get("type") == "hello":
                    session_id = data.get("session_id") or None
                    session = self._runtime.sessions.get_or_create(session_id)
                    session_id = session.id
                    self._clients[session_id] = ws
                    await ws.send_json(
                        {
                            "event": "welcome",
                            "payload": {
                                "session_id": session.id,
                                "sessions": self._runtime.sessions.list(),
                                "tools": self._runtime.registry.names,
                                "model": self._runtime.settings.model,
                            },
                        }
                    )
                elif data.get("type") == "chat" and session_id:
                    text = (data.get("text") or "").strip()
                    if text:
                        await self._bus.publish(InboundMessage(session_id=session_id, text=text))
                elif data.get("type") == "approval":
                    approval_id = data.get("approval_id")
                    if approval_id:
                        self._runtime.approvals.respond(approval_id, bool(data.get("approved")))
        finally:
            if session_id:
                self._clients.pop(session_id, None)
        return ws

    # ---- REST: sessions -----------------------------------------------
    async def _list_sessions(self, request: web.Request) -> web.Response:
        return _json({"sessions": self._runtime.sessions.list()})

    async def _create_session(self, request: web.Request) -> web.Response:
        session = self._runtime.sessions.create()
        return _json({"id": session.id})

    async def _session_messages(self, request: web.Request) -> web.Response:
        session = self._runtime.sessions.get(request.match_info["sid"])
        if session is None:
            return _json({"error": "session not found"}, status=404)
        return _json({"messages": [m.to_dict() for m in session.messages]})

    async def _delete_session(self, request: web.Request) -> web.Response:
        ok = self._runtime.sessions.delete(request.match_info["sid"])
        return _json({"deleted": ok})

    # ---- REST: memory --------------------------------------------------
    async def _list_memory(self, request: web.Request) -> web.Response:
        entries = await self._runtime.memory.all()
        return _json(
            {
                "count": len(entries),
                "semantic_enabled": self._runtime.memory.semantic_enabled,
                "entries": [
                    {"id": e.id, "kind": e.kind, "content": e.content, "created_at": e.created_at}
                    for e in entries
                ],
            }
        )

    async def _clear_memory(self, request: web.Request) -> web.Response:
        count = await self._runtime.memory.clear()
        return _json({"cleared": count})

    # ---- OpenAI-compatible endpoint -----------------------------------
    async def _openai_completions(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _json({"error": "invalid JSON body"}, status=400)

        messages: list[dict] = body.get("messages") or []
        if not messages:
            return _json({"error": "messages required"}, status=400)

        tools = body.get("tools")
        result = await self._runtime.provider.complete(
            messages,
            body.get("model") or self._runtime.settings.model,
            tools=tools or None,
            temperature=body.get("temperature", 0.7),
        )

        message: dict[str, Any] = {"role": "assistant", "content": result.content or ""}
        if result.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in result.tool_calls
            ]
        return _json(
            {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or self._runtime.settings.model,
                "choices": [{"index": 0, "message": message, "finish_reason": result.finish_reason}],
            }
        )


# ----------------------------------------------------------------------
def _json(data: dict, *, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


@web.middleware
async def _cors_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response
