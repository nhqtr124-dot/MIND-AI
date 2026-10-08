"""Anthropic Messages API adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ..errors import ProviderError
from ..types import ChatRequest, DiscoveredModel, StreamEvent, Usage
from .base import ProviderAdapter

API_VERSION = "2023-06-01"


def _messages(req: ChatRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role == "system":
            continue
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": i.mime_type, "data": i.data_b64}} for i in m.images
        ]
        content.append({"type": "text", "text": m.text or " "})
        out.append({"role": m.role, "content": content})
    return out


class AnthropicAdapter(ProviderAdapter):
    kind = "anthropic"
    default_base_url = "https://api.anthropic.com"

    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}

    async def list_models(self) -> list[DiscoveredModel]:
        out: list[DiscoveredModel] = []
        after: str | None = None
        for _ in range(20):
            params = {"limit": "100", **({"after_id": after} if after else {})}
            data = await self._request_json("GET", f"{self.base_url}/v1/models", params=params)
            for m in data.get("data", []):
                caps = {"chat", "vision", "tools", "code"}  # every current Claude model accepts images and tools
                out.append(
                    DiscoveredModel(
                        name=m["id"],
                        display_name=m.get("display_name") or m["id"],
                        capabilities=caps,
                        context_window=m.get("max_input_tokens"),
                        max_output_tokens=m.get("max_tokens"),
                        capabilities_source="provider_family",
                    )
                )
            if not data.get("has_more"):
                break
            after = data.get("last_id")
        return out

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:  # type: ignore[override]
        system = "\n\n".join(filter(None, [req.system, *[m.text for m in req.messages if m.role == "system"]]))
        body: dict[str, Any] = {"model": req.model, "max_tokens": req.max_output_tokens, "messages": _messages(req), "stream": True}
        if system:
            body["system"] = system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        usage = Usage()
        finish: str | None = None
        async for event, data in self._sse(f"{self.base_url}/v1/messages", body):
            try:
                payload = json.loads(data)
            except ValueError:
                continue
            etype = payload.get("type", event)
            if etype == "message_start":
                u = (payload.get("message") or {}).get("usage") or {}
                usage.input_tokens = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(
                    u.get("cache_creation_input_tokens") or 0
                )
                usage.output_tokens = int(u.get("output_tokens") or 0)
            elif etype == "content_block_delta":
                delta = payload.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield StreamEvent("delta", text=delta["text"])
            elif etype == "message_delta":
                u = payload.get("usage") or {}
                if "output_tokens" in u:
                    usage.output_tokens = int(u["output_tokens"])
                finish = (payload.get("delta") or {}).get("stop_reason") or finish
            elif etype == "error":
                err = payload.get("error") or {}
                kind = "rate_limit" if err.get("type") == "rate_limit_error" else "unavailable" if err.get("type") == "overloaded_error" else "unknown"
                raise ProviderError(kind, f"anthropic stream error: {err.get('message', 'unknown')}", provider=self.kind)  # type: ignore[arg-type]
            elif etype == "message_stop":
                break
        yield StreamEvent("usage", usage=usage)
        yield StreamEvent("done", finish_reason=finish)
