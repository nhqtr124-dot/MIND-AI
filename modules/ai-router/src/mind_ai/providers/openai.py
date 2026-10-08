"""OpenAI Chat Completions adapter, also used for OpenAI-compatible servers
(vLLM, LM Studio, llama.cpp server, Ollama's /v1 endpoint, OpenRouter, Groq, ...)."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from ..errors import ProviderError
from ..types import ChatRequest, DiscoveredModel, EmbeddingResult, ImageResult, StreamEvent, Usage
from .base import ProviderAdapter

_NON_CHAT_MARKERS = ("embedding", "tts", "whisper", "dall-e", "moderation", "transcribe", "realtime", "audio", "image", "search", "davinci", "babbage")


def _messages(req: ChatRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if req.system:
        out.append({"role": "system", "content": req.system})
    for m in req.messages:
        if m.images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": m.text}]
            parts += [{"type": "image_url", "image_url": {"url": f"data:{i.mime_type};base64,{i.data_b64}"}} for i in m.images]
            out.append({"role": m.role, "content": parts})
        else:
            out.append({"role": m.role, "content": m.text})
    return out


class OpenAIAdapter(ProviderAdapter):
    kind = "openai"
    default_base_url = "https://api.openai.com/v1"
    supports_embeddings = True
    supports_images = True
    # OpenAI's current API takes max_completion_tokens; many compatible servers only accept max_tokens.
    max_tokens_field = "max_completion_tokens"

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_models(self) -> list[DiscoveredModel]:
        data = await self._request_json("GET", f"{self.base_url}/models")
        out = []
        for m in data.get("data", []):
            mid = str(m.get("id", ""))
            if not mid:
                continue
            low = mid.lower()
            caps: set[str] = set()
            if "embedding" in low:
                caps.add("embeddings")
            elif any(k in low for k in ("dall-e", "gpt-image")):
                caps.add("image_generation")
            elif not any(k in low for k in _NON_CHAT_MARKERS):
                caps.add("chat")
            if caps:
                out.append(DiscoveredModel(name=mid, display_name=mid, capabilities=caps, capabilities_source="heuristic"))
        return sorted(out, key=lambda d: d.name)

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:  # type: ignore[override]
        body: dict[str, Any] = {
            "model": req.model,
            "messages": _messages(req),
            "stream": True,
            "stream_options": {"include_usage": True},
            self.max_tokens_field: req.max_output_tokens,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        usage: Usage | None = None
        finish: str | None = None
        async for _event, data in self._sse(f"{self.base_url}/chat/completions", body):
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if "error" in chunk:
                err = chunk["error"]
                raise ProviderError("unknown", f"{self.kind} stream error: {err.get('message', err) if isinstance(err, dict) else err}", provider=self.kind)
            for choice in chunk.get("choices") or []:
                delta = (choice.get("delta") or {}).get("content")
                if delta:
                    yield StreamEvent("delta", text=delta)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
            u = chunk.get("usage")
            if u:
                usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        if usage is not None:
            yield StreamEvent("usage", usage=usage)
        yield StreamEvent("done", finish_reason=finish)

    async def embed(self, model: str, texts: list[str]) -> EmbeddingResult:
        data = await self._request_json("POST", f"{self.base_url}/embeddings", json={"model": model, "input": texts})
        rows = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
        u = data.get("usage") or {}
        return EmbeddingResult([r["embedding"] for r in rows], Usage(int(u.get("prompt_tokens") or 0), 0))

    async def generate_image(self, model: str, prompt: str, size: str = "1024x1024", n: int = 1) -> ImageResult:
        data = await self._request_json(
            "POST", f"{self.base_url}/images/generations", json={"model": model, "prompt": prompt, "size": size, "n": n}
        )
        images: list[bytes] = []
        revised = None
        for item in data.get("data", []):
            revised = item.get("revised_prompt") or revised
            if item.get("b64_json"):
                images.append(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                resp = await self.client.get(item["url"])
                if resp.status_code >= 400:
                    raise ProviderError("unavailable", f"could not download generated image (HTTP {resp.status_code})", provider=self.kind)
                images.append(resp.content)
        if not images:
            raise ProviderError("unknown", "provider returned no image data", provider=self.kind)
        return ImageResult(images=images, mime_type="image/png", revised_prompt=revised)


class OpenAICompatibleAdapter(OpenAIAdapter):
    kind = "openai_compatible"
    default_base_url = "http://localhost:11434/v1"
    max_tokens_field = "max_tokens"
