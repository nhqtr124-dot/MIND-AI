from decimal import Decimal

import pytest
from mind_ai import (
    ChatMessage,
    ImagePart,
    ModelCandidate,
    NoEligibleModel,
    Pricing,
    Usage,
    classify,
    compute_cost,
    select_model,
)


def cands() -> list[ModelCandidate]:
    return [
        ModelCandidate(
            "fast",
            "openai",
            "small",
            {"chat"},
            16000,
            quality_tier=2,
            speed_tier=5,
            input_price_per_mtok=Decimal("0.1"),
            output_price_per_mtok=Decimal("0.4"),
        ),
        ModelCandidate(
            "smart",
            "anthropic",
            "large",
            {"chat", "vision", "code"},
            200000,
            quality_tier=5,
            speed_tier=2,
            input_price_per_mtok=Decimal("3"),
            output_price_per_mtok=Decimal("15"),
        ),
        ModelCandidate(
            "off", "gemini", "disabled", {"chat", "vision"}, 1000000, quality_tier=5, enabled=False
        ),
    ]


def test_classify() -> None:
    assert classify([ChatMessage("user", "hi")]).task_type == "simple"
    assert (
        classify([ChatMessage("user", "Fix this python traceback in my function please")]).task_type == "code"
    )
    assert (
        classify(
            [ChatMessage("user", "Compare the trade-offs of these three database designs for our team")]
        ).task_type
        == "reasoning"
    )
    assert classify([ChatMessage("user", "x", images=[ImagePart("", "image/png")])]).needs_vision


def test_simple_prefers_fast_cheap_and_code_prefers_quality() -> None:
    assert select_model(classify([ChatMessage("user", "hi")]), cands()).model.id == "fast"
    d = select_model(classify([ChatMessage("user", "Refactor this python class to remove the bug")]), cands())
    assert d.model.id == "smart" and "code" in d.reason
    assert d.rejected["off"] == "disabled"


def test_vision_and_context_are_hard_requirements() -> None:
    d = select_model(classify([ChatMessage("user", "hi", images=[ImagePart("", "image/png")])]), cands())
    assert d.model.id == "smart" and d.rejected["fast"] == "no image input"
    long = classify([ChatMessage("user", "word " * 30000)])
    assert select_model(long, cands()).model.id == "smart"


def test_no_eligible_model() -> None:
    with pytest.raises(NoEligibleModel) as ei:
        select_model(classify([ChatMessage("user", "hi", images=[ImagePart("", "image/png")])]), cands()[:1])
    assert ei.value.rejected == {"fast": "no image input"}


def test_cost() -> None:
    assert compute_cost(Usage(1_000_000, 500_000), Pricing(Decimal("3"), Decimal("15"))) == Decimal("10.5")
    assert compute_cost(Usage(10, 10), Pricing()) is None
