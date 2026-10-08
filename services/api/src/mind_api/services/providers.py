"""Provider credentials, model discovery and routing candidates."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from mind_ai import ModelCandidate, ProviderAdapter, ProviderError, make_adapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import ModelConfig, ModelProvider
from ..security import decrypt_secret

# Tests replace this to route provider HTTP through a mock transport.
HTTP_CLIENT_FACTORY: Callable[[], httpx.AsyncClient] | None = None

PROVIDER_KINDS: dict[str, dict[str, Any]] = {
    "openai": {"label": "OpenAI", "needs_key": True, "default_base_url": "https://api.openai.com/v1"},
    "anthropic": {"label": "Anthropic", "needs_key": True, "default_base_url": "https://api.anthropic.com"},
    "gemini": {"label": "Google Gemini", "needs_key": True, "default_base_url": "https://generativelanguage.googleapis.com/v1beta"},
    "openai_compatible": {
        "label": "OpenAI-compatible (Ollama, vLLM, LM Studio, OpenRouter, ...)",
        "needs_key": False,
        "default_base_url": "http://localhost:11434/v1",
    },
}


def adapter_for(provider: ModelProvider) -> ProviderAdapter:
    client = HTTP_CLIENT_FACTORY() if HTTP_CLIENT_FACTORY else None
    return make_adapter(provider.kind, decrypt_secret(provider.api_key_encrypted), provider.base_url, client=client)


async def discover(provider: ModelProvider) -> list[dict[str, Any]]:
    """Call the provider's model list endpoint. Raises ProviderError on failure."""
    adapter = adapter_for(provider)
    try:
        models = await adapter.list_models()
    finally:
        await adapter.aclose()
    return [
        {
            "name": m.name,
            "display_name": m.display_name,
            "capabilities": sorted(m.capabilities),
            "capabilities_source": m.capabilities_source,
            "context_window": m.context_window,
            "max_output_tokens": m.max_output_tokens,
        }
        for m in models
    ]


def apply_discovery(db: Session, provider: ModelProvider, found: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert discovered models. New models start disabled so cost is opt-in."""
    existing = {m.model_name: m for m in db.scalars(select(ModelConfig).where(ModelConfig.provider_id == provider.id))}
    added = 0
    for m in found:
        cfg = existing.get(m["name"])
        if cfg is None:
            db.add(
                ModelConfig(
                    org_id=provider.org_id,
                    provider_id=provider.id,
                    model_name=m["name"],
                    display_name=m["display_name"],
                    capabilities=m["capabilities"],
                    capabilities_source=m["capabilities_source"],
                    context_window=m["context_window"],
                    max_output_tokens=m["max_output_tokens"],
                    enabled=False,
                )
            )
            added += 1
        else:
            # Refresh provider-reported limits; keep admin-edited capabilities, tiers and prices.
            cfg.context_window = m["context_window"] or cfg.context_window
            cfg.max_output_tokens = m["max_output_tokens"] or cfg.max_output_tokens
    provider.status, provider.last_error, provider.last_checked_at = "ok", None, utcnow()
    db.flush()
    return added, len(found)


def mark_error(provider: ModelProvider, err: ProviderError) -> None:
    provider.status, provider.last_error, provider.last_checked_at = "error", err.message, utcnow()


def candidate(m: ModelConfig) -> ModelCandidate:
    return ModelCandidate(
        id=str(m.id),
        provider_kind=m.provider.kind,
        model_name=m.model_name,
        capabilities=set(m.capabilities),
        context_window=m.context_window,
        quality_tier=m.quality_tier,
        speed_tier=m.speed_tier,
        input_price_per_mtok=m.input_price_per_mtok,
        output_price_per_mtok=m.output_price_per_mtok,
        enabled=m.enabled,
        healthy=m.provider.status != "error",
    )


def org_models(db: Session, org_id: uuid.UUID, capability: str | None = None, enabled_only: bool = True) -> list[ModelConfig]:
    q = select(ModelConfig).where(ModelConfig.org_id == org_id)
    if enabled_only:
        q = q.where(ModelConfig.enabled.is_(True))
    rows = list(db.scalars(q.order_by(ModelConfig.model_name)).unique())
    return [m for m in rows if capability is None or capability in m.capabilities]
