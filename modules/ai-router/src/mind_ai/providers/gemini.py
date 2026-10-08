"""Google Gemini API (generativelanguage.googleapis.com, v1beta) adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ..errors import ProviderError
from ..types import ChatRequest, DiscoveredModel, EmbeddingResult, StreamEvent, Usage
from .base import ProviderAdapter


def _contents(req: ChatRequest) -> list[dict[str, Any]]:
    out = []
    for m in req.messages:
        if m.role == "system":
            continue
        parts: list[dict[str, Any]] = [
            {"inline_data": {"mime_type": i.mime_type, "data": i.data_b64}} for i in m.images
        ]
        parts.append({"text": m.text})
        out.append({"role": "model" if m.role == "assistant" else "user", "parts": parts})
    return out


class GeminiAdapter(ProviderAdapter):
    kind = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    supports_embeddings = True

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    async def list_models(self) -> list[DiscoveredModel]:
        out: list[DiscoveredModel] = []
        token: str | None = None
        for _ in range(20):
            params = {"pageSize": "1000", **({"pageToken": token} if token else {})}
            data = await self._request_json("GET", f"{self.base_url}/models", params=params)
            for m in data.get("models", []):
                methods = set(m.get("supportedGenerationMethods") or [])
                caps: set[str] = set()
                if "generateContent" in methods:
                    caps |= {"chat", "vision"}
                if "embedContent" in methods or "batchEmbedContents" in methods:
                    caps.add("embeddings")
                if not caps:
                    continue
                name = str(m.get("name", "")).removeprefix("models/")
                out.append(
                    DiscoveredModel(
                        name=name,
                        display_name=m.get("displayName") or name,
                        capabilities=caps,
                        context_window=m.get("inputTokenLimit"),
                        max_output_tokens=m.get("outputTokenLimit"),
                        capabilities_source="provider",
                    )
                )
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:  # type: ignore[override]
        system = "\n\n".join(
            filter(None, [req.system, *[m.text for m in req.messages if m.role == "system"]])
        )
        body: dict[str, Any] = {
            "contents": _contents(req),
            "generationConfig": {"maxOutputTokens": req.max_output_tokens},
        }
        if req.temperature is not None:
            body["generationConfig"]["temperature"] = req.temperature
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        usage: Usage | None = None
        finish: str | None = None
        url = f"{self.base_url}/models/{req.model}:streamGenerateContent"
        async for _event, data in self._sse(url, body, params={"alt": "sse"}):
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if "error" in chunk:
                raise ProviderError(
                    "unknown", f"gemini stream error: {chunk['error'].get('message')}", provider=self.kind
                )
            for cand in chunk.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("text") and not part.get("thought"):
                        yield StreamEvent("delta", text=part["text"])
                finish = cand.get("finishReason") or finish
            um = chunk.get("usageMetadata")
            if um:
                usage = Usage(
                    int(um.get("promptTokenCount") or 0),
                    int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0),
                )
        if usage is not None:
            yield StreamEvent("usage", usage=usage)
        yield StreamEvent("done", finish_reason=finish)

    async def embed(self, model: str, texts: list[str]) -> EmbeddingResult:
        body = {
            "requests": [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in texts]
        }
        data = await self._request_json(
            "POST", f"{self.base_url}/models/{model}:batchEmbedContents", json=body
        )
        return EmbeddingResult([e["values"] for e in data.get("embeddings", [])], Usage())
