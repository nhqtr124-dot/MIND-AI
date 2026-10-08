"""Automatic model selection.

Routing is deterministic and explainable: candidates are filtered by hard
requirements (capabilities, context window) and ranked by a weighted score of
admin-assigned quality/speed tiers, configured price and task fit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .types import ChatMessage, estimate_tokens

TaskType = Literal["code", "reasoning", "creative", "translation", "simple", "general"]
Preference = Literal["balanced", "quality", "speed", "cost"]


@dataclass
class ModelCandidate:
    id: str
    provider_kind: str
    model_name: str
    capabilities: set[str]
    context_window: int | None = None
    quality_tier: int = 3
    speed_tier: int = 3
    input_price_per_mtok: Decimal | None = None
    output_price_per_mtok: Decimal | None = None
    enabled: bool = True
    healthy: bool = True


@dataclass
class TaskProfile:
    task_type: TaskType
    needs_vision: bool = False
    needs_tools: bool = False
    est_input_tokens: int = 0
    max_output_tokens: int = 2048
    preference: Preference = "balanced"


@dataclass
class RoutingDecision:
    model: ModelCandidate
    reason: str
    profile: TaskProfile
    ranked: list[tuple[str, float]] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)


class NoEligibleModel(Exception):
    def __init__(self, message: str, rejected: dict[str, str]) -> None:
        super().__init__(message)
        self.rejected = rejected


_CODE = re.compile(r"```|\b(def|class|function|const|import|traceback|stack trace|compile|bug|refactor|typescript|python|sql|regex|api)\b", re.I)
_REASON = re.compile(r"\b(prove|derive|step by step|analy[sz]e|compare|trade-?offs?|why|calculate|solve|plan|strategy|evaluate)\b", re.I)
_CREATIVE = re.compile(r"\b(story|poem|slogan|creative|brainstorm|lyrics|fiction|tagline)\b", re.I)
_TRANSLATE = re.compile(r"\b(translate|translation|ترجم)\b", re.I)


def classify(messages: list[ChatMessage], system: str | None = None, max_output_tokens: int = 2048, preference: Preference = "balanced") -> TaskProfile:
    last = next((m for m in reversed(messages) if m.role == "user"), None)
    text = last.text if last else ""
    total = estimate_tokens((system or "") + "".join(m.text for m in messages)) + 800 * sum(len(m.images) for m in messages)
    if _TRANSLATE.search(text):
        t: TaskType = "translation"
    elif _CODE.search(text):
        t = "code"
    elif _REASON.search(text) or len(text) > 1500:
        t = "reasoning"
    elif _CREATIVE.search(text):
        t = "creative"
    elif len(text) < 120:
        t = "simple"
    else:
        t = "general"
    return TaskProfile(
        task_type=t,
        needs_vision=any(m.images for m in messages),
        est_input_tokens=total,
        max_output_tokens=max_output_tokens,
        preference=preference,
    )


_WEIGHTS: dict[str, tuple[float, float, float]] = {
    # (quality, speed, cost)
    "quality": (0.75, 0.10, 0.15),
    "speed": (0.25, 0.60, 0.15),
    "cost": (0.25, 0.15, 0.60),
    "balanced": (0.50, 0.25, 0.25),
}
_TASK_SHIFT: dict[str, tuple[float, float, float]] = {
    "code": (0.15, -0.05, -0.10),
    "reasoning": (0.15, -0.05, -0.10),
    "simple": (-0.20, 0.10, 0.10),
}


def _blended_price(c: ModelCandidate) -> Decimal | None:
    if c.input_price_per_mtok is None or c.output_price_per_mtok is None:
        return None
    return c.input_price_per_mtok * 3 + c.output_price_per_mtok  # weight typical 3:1 input/output ratio


def select_model(profile: TaskProfile, candidates: list[ModelCandidate]) -> RoutingDecision:
    rejected: dict[str, str] = {}
    eligible: list[ModelCandidate] = []
    need = profile.est_input_tokens + profile.max_output_tokens
    for c in candidates:
        if not c.enabled:
            rejected[c.id] = "disabled"
        elif not c.healthy:
            rejected[c.id] = "provider unhealthy"
        elif "chat" not in c.capabilities:
            rejected[c.id] = "not a chat model"
        elif profile.needs_vision and "vision" not in c.capabilities:
            rejected[c.id] = "no image input"
        elif profile.needs_tools and "tools" not in c.capabilities:
            rejected[c.id] = "no tool use"
        elif c.context_window is not None and c.context_window < need:
            rejected[c.id] = f"context window {c.context_window} < {need} tokens needed"
        else:
            eligible.append(c)
    if not eligible:
        raise NoEligibleModel("No configured model satisfies this request", rejected)

    prices = [p for p in (_blended_price(c) for c in eligible) if p is not None]
    lo, hi = (min(prices), max(prices)) if prices else (Decimal(0), Decimal(0))
    wq, ws, wc = _WEIGHTS[profile.preference]
    dq, ds, dc = _TASK_SHIFT.get(profile.task_type, (0.0, 0.0, 0.0))
    wq, ws, wc = max(wq + dq, 0.05), max(ws + ds, 0.05), max(wc + dc, 0.05)

    scored: list[tuple[float, ModelCandidate]] = []
    for c in eligible:
        p = _blended_price(c)
        if p is None:
            cost_score = 0.5  # unknown price: neutral, never assumed free
        elif hi == lo:
            cost_score = 1.0
        else:
            cost_score = float((hi - p) / (hi - lo))
        fit = 0.05 if profile.task_type in c.capabilities else 0.0
        unknown_ctx_penalty = 0.05 if c.context_window is None and need > 8000 else 0.0
        score = wq * (c.quality_tier - 1) / 4 + ws * (c.speed_tier - 1) / 4 + wc * cost_score + fit - unknown_ctx_penalty
        scored.append((round(score, 4), c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].model_name))
    best = scored[0][1]
    reason = (
        f"Task looks like '{profile.task_type}' (~{profile.est_input_tokens} input tokens"
        f"{', with images' if profile.needs_vision else ''}); preference '{profile.preference}'. "
        f"Chose {best.model_name} (quality tier {best.quality_tier}, speed tier {best.speed_tier}"
        f"{', price not configured' if _blended_price(best) is None else ''}) from {len(eligible)} eligible model(s)."
    )
    return RoutingDecision(best, reason, profile, [(c.id, s) for s, c in scored], rejected)
