"""Provider-neutral request/response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Role = Literal["system", "user", "assistant"]
Capability = Literal[
    "chat", "vision", "tools", "embeddings", "image_generation", "code", "reasoning", "long_context"
]


@dataclass
class ImagePart:
    data_b64: str
    mime_type: str


@dataclass
class ChatMessage:
    role: Role
    text: str
    images: list[ImagePart] = field(default_factory=list)


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    system: str | None = None
    max_output_tokens: int = 2048
    temperature: float | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class StreamEvent:
    type: Literal["delta", "usage", "done"]
    text: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None


@dataclass
class DiscoveredModel:
    name: str
    display_name: str
    capabilities: set[str]
    context_window: int | None = None
    max_output_tokens: int | None = None
    capabilities_source: Literal["provider", "provider_family", "heuristic"] = "heuristic"


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    usage: Usage


@dataclass
class ImageResult:
    images: list[bytes]
    mime_type: str
    revised_prompt: str | None = None


@dataclass
class Pricing:
    input_per_mtok: Decimal | None = None
    output_per_mtok: Decimal | None = None
    per_image: Decimal | None = None

    @property
    def known(self) -> bool:
        return self.input_per_mtok is not None and self.output_per_mtok is not None


def compute_cost(usage: Usage, pricing: Pricing) -> Decimal | None:
    """Return USD cost, or None when the admin has not configured pricing for this model."""
    if not pricing.known:
        return None
    assert pricing.input_per_mtok is not None and pricing.output_per_mtok is not None
    mtok = Decimal(1_000_000)
    return (
        Decimal(usage.input_tokens) * pricing.input_per_mtok
        + Decimal(usage.output_tokens) * pricing.output_per_mtok
    ) / mtok


def estimate_tokens(text: str) -> int:
    """Rough estimate (about 4 characters per token for English); used only for budgeting, never billing."""
    return max(1, len(text) // 4)
