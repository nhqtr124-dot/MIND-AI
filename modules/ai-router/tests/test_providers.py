"""Contract tests: they pin request/response shapes against mocked HTTP.

They prove the adapters build documented requests and parse documented responses;
they do NOT prove a live provider works (see docs/TESTING.md).
"""

import json
from collections.abc import Callable

import httpx
import pytest

from mind_ai import ChatMessage, ChatRequest, ImagePart, ProviderError, make_adapter, with_retries


def sse(*events: tuple[str | None, object]) -> bytes:
    out = []
    for ev, data in events:
        if ev:
            out.append(f"event: {ev}")
        out.append("data: " + (data if isinstance(data, str) else json.dumps(data)))
        out.append("")
    return ("\n".join(out) + "\n").encode()


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


REQ = ChatRequest(
    model="m1",
    system="be brief",
    messages=[ChatMessage("user", "hi", images=[ImagePart("aGVsbG8=", "image/png")])],
    max_output_tokens=50,
)


async def test_openai_stream() -> None:
    seen: dict = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(r.url), r.headers["authorization"], json.loads(r.content)
        body = sse(
            (None, {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]}),
            (None, {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
            (None, {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}}),
            (None, "[DONE]"),
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    a = make_adapter("openai", "sk-test", client=client(h))
    text, usage, finish = await a.complete(REQ)
    assert (text, usage.input_tokens, usage.output_tokens, finish) == ("Hello", 12, 2, "stop")
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    b = seen["body"]
    assert b["stream"] is True and b["stream_options"] == {"include_usage": True}
    assert b["max_completion_tokens"] == 50 and "temperature" not in b
    assert b["messages"][0] == {"role": "system", "content": "be brief"}
    assert b["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_openai_compatible_uses_max_tokens_and_base_url() -> None:
    seen: dict = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(r.url), json.loads(r.content)
        return httpx.Response(200, content=sse((None, {"choices": [{"delta": {"content": "x"}}]}), (None, "[DONE]")))

    a = make_adapter("openai_compatible", None, "http://local:8080/v1", client=client(h))
    text, usage, _ = await a.complete(REQ)
    assert text == "x" and usage.total == 0
    assert seen["url"] == "http://local:8080/v1/chat/completions" and seen["body"]["max_tokens"] == 50


async def test_openai_models_filtering() -> None:
    def h(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "chat-a"}, {"id": "text-embedding-x"}, {"id": "whisper-1"}, {"id": "gpt-image-z"}]})

    models = {m.name: m.capabilities for m in await make_adapter("openai", "k", client=client(h)).list_models()}
    assert models == {"chat-a": {"chat"}, "text-embedding-x": {"embeddings"}, "gpt-image-z": {"image_generation"}}


async def test_anthropic_stream() -> None:
    seen: dict = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["headers"], seen["body"], seen["url"] = r.headers, json.loads(r.content), str(r.url)
        body = sse(
            ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 20, "output_tokens": 1}}}),
            ("ping", {"type": "ping"}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there"}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}),
            ("message_stop", {"type": "message_stop"}),
        )
        return httpx.Response(200, content=body)

    a = make_adapter("anthropic", "ak", client=client(h))
    text, usage, finish = await a.complete(REQ)
    assert (text, usage.input_tokens, usage.output_tokens, finish) == ("Hi there", 20, 3, "end_turn")
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "ak" and seen["headers"]["anthropic-version"] == "2023-06-01"
    b = seen["body"]
    assert b["system"] == "be brief" and b["max_tokens"] == 50
    assert b["messages"][0]["content"][0]["type"] == "image"
    assert b["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


async def test_anthropic_stream_error_event() -> None:
    def h(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})))

    with pytest.raises(ProviderError) as ei:
        await make_adapter("anthropic", "ak", client=client(h)).complete(REQ)
    assert ei.value.kind == "unavailable" and ei.value.retryable


async def test_anthropic_models_pagination() -> None:
    calls = []

    def h(r: httpx.Request) -> httpx.Response:
        calls.append(dict(r.url.params))
        if "after_id" not in r.url.params:
            return httpx.Response(200, json={"data": [{"id": "a", "display_name": "A"}], "has_more": True, "last_id": "a"})
        return httpx.Response(200, json={"data": [{"id": "b", "display_name": "B"}], "has_more": False})

    models = await make_adapter("anthropic", "k", client=client(h)).list_models()
    assert [m.name for m in models] == ["a", "b"] and "vision" in models[0].capabilities
    assert calls[1]["after_id"] == "a"


async def test_gemini_stream_and_models() -> None:
    seen: dict = {}

    def h(r: httpx.Request) -> httpx.Response:
        if r.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "models/g-chat", "displayName": "G", "inputTokenLimit": 1000, "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/g-embed", "supportedGenerationMethods": ["embedContent"]},
                        {"name": "models/other", "supportedGenerationMethods": ["predict"]},
                    ]
                },
            )
        seen["url"], seen["key"], seen["body"] = str(r.url), r.headers["x-goog-api-key"], json.loads(r.content)
        return httpx.Response(
            200,
            content=sse(
                (None, {"candidates": [{"content": {"parts": [{"text": "Bon"}]}}]}),
                (None, {"candidates": [{"content": {"parts": [{"text": "jour"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}}),
            ),
        )

    a = make_adapter("gemini", "gk", client=client(h))
    models = {m.name: m for m in await a.list_models()}
    assert set(models) == {"g-chat", "g-embed"} and models["g-chat"].context_window == 1000
    text, usage, finish = await a.complete(REQ)
    assert (text, usage.input_tokens, usage.output_tokens, finish) == ("Bonjour", 7, 2, "STOP")
    assert seen["url"].endswith("/models/m1:streamGenerateContent?alt=sse") and seen["key"] == "gk"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert seen["body"]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/png"


@pytest.mark.parametrize(("status", "kind", "retryable"), [(401, "auth", False), (429, "rate_limit", True), (500, "unavailable", True), (400, "invalid_request", False)])
async def test_http_errors_are_categorised_and_redacted(status: int, kind: str, retryable: bool) -> None:
    def h(r: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "bad key sk-secret-123"}})

    with pytest.raises(ProviderError) as ei:
        await make_adapter("openai", "sk-secret-123", client=client(h)).complete(REQ)
    assert ei.value.kind == kind and ei.value.retryable is retryable
    assert "sk-secret-123" not in ei.value.message


async def test_network_error() -> None:
    def h(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as ei:
        await make_adapter("anthropic", "k", client=client(h)).list_models()
    assert ei.value.kind == "network"


async def test_retries_only_retryable() -> None:
    n = {"c": 0}

    async def flaky() -> str:
        n["c"] += 1
        if n["c"] < 3:
            raise ProviderError("rate_limit", "slow down")
        return "ok"

    assert await with_retries(flaky, attempts=3, base_delay=0) == "ok"

    async def fatal() -> str:
        n["c"] += 1
        raise ProviderError("auth", "bad key")

    n["c"] = 0
    with pytest.raises(ProviderError):
        await with_retries(fatal, attempts=3, base_delay=0)
    assert n["c"] == 1


async def test_openai_embeddings_and_images() -> None:
    def h(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.2]}, {"index": 0, "embedding": [0.1]}], "usage": {"prompt_tokens": 4}})
        return httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgo="}]})

    a = make_adapter("openai", "k", client=client(h))
    e = await a.embed("emb", ["a", "b"])
    assert e.vectors == [[0.1], [0.2]] and e.usage.input_tokens == 4
    img = await a.generate_image("img", "a cat")
    assert img.images[0].startswith(b"\x89PNG")
