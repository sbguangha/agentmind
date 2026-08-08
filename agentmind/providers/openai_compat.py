"""OpenAI-compatible chat + embeddings client built directly on aiohttp.

Talking raw HTTP (instead of pulling in the ``openai`` SDK) keeps the
dependency footprint tiny and shows exactly what happens on the wire, which
makes it trivial to point the agent at OpenAI, DeepSeek, Ollama, vLLM,
LocalAI or any other OpenAI-compatible endpoint.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import aiohttp

from agentmind.providers.base import (
    ChatResult,
    LLMProvider,
    ProviderError,
    StreamChunk,
    ToolCall,
    ToolCallDelta,
)


class OpenAICompatProvider(LLMProvider):
    """A streaming OpenAI-compatible provider."""

    def __init__(self, api_base: str, api_key: str = "", *, timeout: float = 120.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _url(base: str, path: str) -> str:
        return f"{base}/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    async def stream(
        self,
        messages: list[dict],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamChunk]:
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        session = await self._get_session()
        async with session.post(
            self._url(self.api_base, "chat/completions"),
            json=payload,
            headers=self._headers(),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text()) or ""
                raise ProviderError(
                    f"Model API error {resp.status}: {body[:500]}"
                )
            async for line in resp.content:
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if not data or data == b"[DONE]":
                    break
                try:
                    raw = json.loads(data)
                except json.JSONDecodeError:
                    continue
                yield self._parse_chunk(raw)

    @staticmethod
    def _parse_chunk(raw: dict) -> StreamChunk:
        choice = (raw.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason")
        tool_deltas: list[ToolCallDelta] = []
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_deltas.append(
                ToolCallDelta(
                    index=int(tc.get("index", 0)),
                    id=tc.get("id"),
                    name=fn.get("name"),
                    arguments=fn.get("arguments") or "",
                    type=tc.get("type"),
                )
            )
        return StreamChunk(
            content_delta=delta.get("content"),
            tool_calls=tool_deltas,
            finish_reason=finish,
        )

    # ------------------------------------------------------------------
    async def complete(
        self,
        messages: list[dict],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> ChatResult:
        """Non-streaming convenience wrapper around :meth:`stream`."""
        content: list[str] = []
        calls: dict[int, ToolCall] = {}
        finish: str | None = None

        async for chunk in self.stream(
            messages, model, tools=tools, temperature=temperature
        ):
            if chunk.content_delta:
                content.append(chunk.content_delta)
            for delta in chunk.tool_calls:
                call = calls.setdefault(
                    delta.index,
                    ToolCall(
                        id=delta.id or f"call_{delta.index}",
                        name=delta.name or "",
                        arguments="",
                        type=delta.type or "function",
                    ),
                )
                if delta.id:
                    call.id = delta.id
                if delta.name:
                    call.name = delta.name
                call.arguments += delta.arguments
            if chunk.finish_reason:
                finish = chunk.finish_reason

        return ChatResult(
            content="".join(content) or None,
            tool_calls=list(calls.values()),
            finish_reason=finish,
        )

    # ------------------------------------------------------------------
    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        if not model:
            raise ProviderError("No embedding model configured.")
        session = await self._get_session()
        async with session.post(
            self._url(self.api_base, "embeddings"),
            json={"model": model, "input": texts},
            headers=self._headers(),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text()) or ""
                raise ProviderError(f"Embedding API error {resp.status}: {body[:500]}")
            data = (await resp.json()).get("data", [])
            ordered = sorted(data, key=lambda item: item["index"])
            return [item["embedding"] for item in ordered]
