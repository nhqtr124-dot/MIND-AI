from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from ..errors import ProviderError, kind_for_status
from ..types import ChatRequest, DiscoveredModel, EmbeddingResult, ImageResult, StreamEvent, Usage

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


class ProviderAdapter:
    kind: ClassVar[str]
    default_base_url: ClassVar[str]
    supports_embeddings: ClassVar[bool] = False
    supports_images: ClassVar[bool] = False

    def __init__(
        self, api_key: str | None, base_url: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- interface -----------------------------------------------------------------
    def headers(self) -> dict[str, str]:  # pragma: no cover - interface
        raise NotImplementedError

    async def list_models(self) -> list[DiscoveredModel]:  # pragma: no cover - interface
        raise NotImplementedError

    def stream_chat(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:  # pragma: no cover - interface
        raise NotImplementedError

    async def embed(self, model: str, texts: list[str]) -> EmbeddingResult:
        raise ProviderError(
            "unsupported", f"{self.kind} adapter does not implement embeddings", provider=self.kind
        )

    async def generate_image(
        self, model: str, prompt: str, size: str = "1024x1024", n: int = 1
    ) -> ImageResult:
        raise ProviderError(
            "unsupported", f"{self.kind} adapter does not implement image generation", provider=self.kind
        )

    # -- helpers ---------------------------------------------------------------------
    async def complete(self, req: ChatRequest) -> tuple[str, Usage, str | None]:
        text: list[str] = []
        usage = Usage()
        finish: str | None = None
        async for ev in self.stream_chat(req):
            if ev.type == "delta":
                text.append(ev.text)
            elif ev.type == "usage" and ev.usage:
                usage = ev.usage
            elif ev.type == "done":
                finish = ev.finish_reason
        return "".join(text), usage, finish

    def _error_from_response(self, status: int, body: bytes) -> ProviderError:
        msg = ""
        try:
            data = json.loads(body)
            err = data.get("error", data)
            if isinstance(err, dict):
                msg = str(err.get("message") or err.get("type") or "")
            else:
                msg = str(err)
        except (ValueError, AttributeError):
            msg = body[:300].decode(errors="replace")
        msg = msg.replace(self.api_key, "***") if self.api_key else msg
        return ProviderError(
            kind_for_status(status),
            f"{self.kind} returned HTTP {status}: {msg or 'no detail'}",
            status,
            self.kind,
        )

    async def _request_json(self, method: str, url: str, **kw: Any) -> Any:
        try:
            resp = await self.client.request(method, url, headers=self.headers(), **kw)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", f"{self.kind} request timed out", provider=self.kind) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network", f"{self.kind} network error: {type(exc).__name__}", provider=self.kind
            ) from exc
        if resp.status_code >= 400:
            raise self._error_from_response(resp.status_code, resp.content)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(
                "unknown", f"{self.kind} returned a non-JSON response", resp.status_code, self.kind
            ) from exc

    async def _sse(
        self, url: str, body: dict[str, Any], params: dict[str, str] | None = None
    ) -> AsyncIterator[tuple[str, str]]:
        """Yield (event, data) pairs from a server-sent-events response."""
        try:
            async with self.client.stream(
                "POST", url, headers=self.headers(), json=body, params=params
            ) as resp:
                if resp.status_code >= 400:
                    raise self._error_from_response(resp.status_code, await resp.aread())
                event, data_lines = "message", []
                async for line in resp.aiter_lines():
                    if line == "":
                        if data_lines:
                            yield event, "\n".join(data_lines)
                        event, data_lines = "message", []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    yield event, "\n".join(data_lines)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", f"{self.kind} stream timed out", provider=self.kind) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network", f"{self.kind} network error: {type(exc).__name__}", provider=self.kind
            ) from exc


async def with_retries(fn: Any, attempts: int = 3, base_delay: float = 0.5) -> Any:
    """Retry an awaitable factory on retryable ProviderErrors with exponential backoff."""
    last: ProviderError | None = None
    for i in range(attempts):
        try:
            return await fn()
        except ProviderError as exc:
            last = exc
            if not exc.retryable or i == attempts - 1:
                raise
            await asyncio.sleep(base_delay * (2**i))
    assert last is not None
    raise last
