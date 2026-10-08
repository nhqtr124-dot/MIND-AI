from .anthropic import AnthropicAdapter
from .base import ProviderAdapter, with_retries
from .gemini import GeminiAdapter
from .openai import OpenAIAdapter, OpenAICompatibleAdapter

ADAPTERS: dict[str, type[ProviderAdapter]] = {
    a.kind: a for a in (OpenAIAdapter, OpenAICompatibleAdapter, AnthropicAdapter, GeminiAdapter)
}


def make_adapter(
    kind: str, api_key: str | None, base_url: str | None = None, **kw: object
) -> ProviderAdapter:
    try:
        cls = ADAPTERS[kind]
    except KeyError as exc:
        raise ValueError(f"unknown provider kind '{kind}'") from exc
    return cls(api_key, base_url, **kw)  # type: ignore[arg-type]


__all__ = [
    "ADAPTERS",
    "AnthropicAdapter",
    "GeminiAdapter",
    "OpenAIAdapter",
    "OpenAICompatibleAdapter",
    "ProviderAdapter",
    "make_adapter",
    "with_retries",
]
