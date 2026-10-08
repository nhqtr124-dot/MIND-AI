"""One-shot model calls for internal use (agent planning, code generation, research synthesis).

Uses the same auto-routing, usage accounting and budget checks as chat.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mind_ai import (
    ChatMessage,
    ChatRequest,
    NoEligibleModel,
    Pricing,
    ProviderError,
    Usage,
    classify,
    compute_cost,
    estimate_tokens,
    select_model,
)

from ..db import session_scope
from ..models import Organization
from .audit import BudgetExceeded, check_budget, record_usage
from .providers import adapter_for, candidate, org_models


class LLMUnavailable(Exception):
    """No usable model is configured, the budget is spent, or every provider call failed."""


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    usage: Usage
    cost_usd: Decimal | None


async def complete(
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    system: str,
    prompt: str,
    *,
    max_output_tokens: int = 4096,
    preference: str = "quality",
    purpose: str = "internal",
    model_config_id: uuid.UUID | None = None,
) -> LLMResult:
    def _pick() -> list[Any]:
        with session_scope() as db:
            org = db.get(Organization, org_id)
            assert org is not None
            check_budget(db, org, user_id)
            models = org_models(db, org_id, "chat")
            if model_config_id is not None:
                chosen = [m for m in models if m.id == model_config_id]
                if not chosen:
                    raise NoEligibleModel("selected model is not enabled", {})
                return chosen
            profile = classify([ChatMessage("user", prompt)], system, max_output_tokens, preference)  # type: ignore[arg-type]
            d = select_model(profile, [candidate(m) for m in models])
            by_id = {str(m.id): m for m in models}
            return [by_id[cid] for cid, _ in d.ranked[:2]]

    try:
        order = await asyncio.to_thread(_pick)
    except NoEligibleModel as exc:
        raise LLMUnavailable(
            "No enabled chat model is configured. Add a provider in Settings → AI providers."
        ) from exc
    except BudgetExceeded as exc:
        raise LLMUnavailable(str(exc)) from exc

    errors = []
    for m in order:
        adapter = adapter_for(m.provider)
        try:
            text, usage, _ = await adapter.complete(
                ChatRequest(
                    m.model_name,
                    [ChatMessage("user", prompt)],
                    system=system,
                    max_output_tokens=max_output_tokens,
                )
            )
        except ProviderError as exc:
            errors.append(f"{m.model_name}: {exc.message}")
            continue
        finally:
            await adapter.aclose()
        if usage.total == 0:
            usage = Usage(estimate_tokens(system + prompt), estimate_tokens(text))
        cost = compute_cost(usage, Pricing(m.input_price_per_mtok, m.output_price_per_mtok))

        def _rec(mm: Any = m, u: Usage = usage, c: Decimal | None = cost) -> None:
            with session_scope() as db:
                record_usage(
                    db, org_id=org_id, user_id=user_id, category=purpose, provider_kind=mm.provider.kind, model_name=mm.model_name,
                    input_tokens=u.input_tokens, output_tokens=u.output_tokens, cost_usd=c,
                )  # fmt: skip

        await asyncio.to_thread(_rec)
        return LLMResult(text, m.model_name, m.provider.kind, usage, cost)
    raise LLMUnavailable("All model calls failed: " + "; ".join(errors))


def complete_sync(*args: Any, **kwargs: Any) -> LLMResult:
    return asyncio.run(complete(*args, **kwargs))


_FENCE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.S)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply (fenced or bare)."""
    m = _FENCE.search(text)
    candidate_text = m.group(1) if m else text
    start = min([i for i in (candidate_text.find("{"), candidate_text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON found in model output")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(candidate_text[start:])
    return obj
