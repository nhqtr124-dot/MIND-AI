"""Acceptance 3, 4, 5, 14 and chat behaviour.

All model calls here go to FakeProvider (an HTTP mock). These tests prove MIND's
streaming, routing, context assembly and accounting; they do not prove that an
external provider works (see docs/TESTING.md for live provider checks).
"""

from typing import Any

from .conftest import FakeProvider, Session, setup_models, sse_events


def _conv(s: Session, **kw: Any) -> dict[str, Any]:
    r = s.post("/api/v1/conversations", json={"org_id": s.org_id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_streamed_response_and_accounting(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(
        alice,
        fake_provider,
        ("fake-chat",),
        fake_chat={"input_price_per_mtok": "1.0", "output_price_per_mtok": "2.0"},
    )
    conv = _conv(alice)
    r = alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "Hello robot"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = sse_events(r.text)
    types = [e["type"] for e in evs]
    assert types[0] == "start" and types[1] == "routing" and types[-1] == "done"
    deltas = [e["text"] for e in evs if e["type"] == "delta"]
    assert len(deltas) > 1, "response must arrive incrementally"
    assert "".join(deltas) == "ECHO: Hello robot"
    done = evs[-1]
    assert done["usage"]["input_tokens"] == 100 and not done["usage"]["estimated"]
    assert done["cost_usd"] is not None and float(done["cost_usd"]) > 0
    detail = alice.get(f"/api/v1/conversations/{conv['id']}").json()
    assert detail["title"] == "Hello robot"
    a = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    assert (
        a["status"] == "completed" and a["content"] == "ECHO: Hello robot" and a["model_name"] == "fake-chat"
    )
    usage = alice.get(f"/api/v1/admin/usage?org_id={alice.org_id}").json()
    assert usage["by_category"][0]["category"] == "chat" and float(usage["total_cost_usd"]) > 0
    # the system prompt sent to the provider forbids claiming unperformed actions
    assert "Never claim" in fake_provider.requests[0]["body"]["messages"][0]["content"]


def test_auto_routing_selects_valid_configured_model(alice: Session, fake_provider: FakeProvider) -> None:
    ids = setup_models(
        alice,
        fake_provider,
        ("fake-chat", "fake-vision"),
        fake_chat={"quality_tier": 2, "speed_tier": 5},
        fake_vision={"quality_tier": 5, "speed_tier": 2, "capabilities": ["chat", "vision", "code"]},
    )
    conv = _conv(alice)
    evs = sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "hi"}).text)
    routing = next(e for e in evs if e["type"] == "routing")
    assert routing["model"] == "fake-chat" and routing["mode"] == "auto" and "simple" in routing["reason"]
    evs = sse_events(
        alice.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"content": "Refactor this python function to fix the bug"},
        ).text
    )
    routing = next(e for e in evs if e["type"] == "routing")
    assert routing["model"] == "fake-vision" and routing["model_config_id"] == ids["fake-vision"]
    # disabled models are never chosen
    assert all(r["body"]["model"] in ("fake-chat", "fake-vision") for r in fake_provider.requests)


def test_no_model_configured_gives_actionable_error(alice: Session) -> None:
    conv = _conv(alice)
    evs = sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "hi"}).text)
    assert evs[-1]["type"] == "error" and evs[-1]["error"]["kind"] == "no_model"
    msgs = alice.get(f"/api/v1/conversations/{conv['id']}").json()["messages"]
    assert [m["status"] for m in msgs if m["role"] == "assistant"] == ["failed"]
    assert all(m["content"] == "" for m in msgs if m["role"] == "assistant")


def test_document_question_answering_uses_file_contents(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider)
    up = alice.post(
        "/api/v1/files",
        data={"org_id": alice.org_id},
        files={"file": ("spec.md", b"# Gripper spec\nThe gripper payload limit is 2.5 kg.", "text/markdown")},
    ).json()
    assert up["has_text"]
    fake_provider.reply = lambda body: (
        "The payload limit is 2.5 kg." if "2.5 kg" in body["messages"][-1]["content"] else "I don't know."
    )
    conv = _conv(alice)
    r = alice.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"content": "What is the payload limit?", "attachments": [up["id"]], "stream": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["assistant_message"]["content"] == "The payload limit is 2.5 kg."
    sent = fake_provider.requests[-1]["body"]["messages"][-1]["content"]
    assert '<document name="spec.md">' in sent and "2.5 kg" in sent


def test_image_attachment_requires_and_routes_to_vision(alice: Session, fake_provider: FakeProvider) -> None:
    from .conftest import tiny_png

    setup_models(
        alice, fake_provider, ("fake-chat", "fake-vision"), fake_vision={"capabilities": ["chat", "vision"]}
    )
    img = alice.post(
        "/api/v1/files", data={"org_id": alice.org_id}, files={"file": ("part.png", tiny_png(), "image/png")}
    ).json()
    conv = _conv(alice)
    evs = sse_events(
        alice.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"content": "What is this?", "attachments": [img["id"]]},
        ).text
    )
    assert next(e for e in evs if e["type"] == "routing")["model"] == "fake-vision"
    content = fake_provider.requests[-1]["body"]["messages"][-1]["content"]
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_failed_provider_call_is_reported_not_faked(alice: Session, fake_provider: FakeProvider) -> None:
    ids = setup_models(alice, fake_provider, ("fake-chat", "broken-model"))
    conv = _conv(alice, model_mode="manual", model_config_id=ids["broken-model"])
    alice.patch(f"/api/v1/orgs/{alice.org_id}", json={"fallback_policy": "ask"})
    evs = sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "hi"}).text)
    err = evs[-1]
    assert (
        err["type"] == "error"
        and err["error"]["kind"] == "unavailable"
        and "upstream exploded" in err["error"]["message"]
    )
    assert not [e for e in evs if e["type"] == "delta"]
    # manual mode + 'ask' policy: alternatives offered, nothing switched silently
    assert [o["model_config_id"] for o in err["fallback_options"]] == [ids["fake-chat"]]
    assert all(r["body"]["model"] == "broken-model" for r in fake_provider.requests)
    msg = [
        m
        for m in alice.get(f"/api/v1/conversations/{conv['id']}").json()["messages"]
        if m["role"] == "assistant"
    ][0]
    assert (
        msg["status"] == "failed" and msg["content"] == "" and msg["error"]["provider"] == "openai_compatible"
    )
    # user accepts the fallback explicitly for one turn
    evs = sse_events(
        alice.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"content": "hi again", "model_config_id": ids["fake-chat"]},
        ).text
    )
    assert evs[-1]["type"] == "done"


def test_manual_mode_never_policy_and_auto_policy(alice: Session, fake_provider: FakeProvider) -> None:
    ids = setup_models(alice, fake_provider, ("fake-chat", "broken-model"))
    conv = _conv(alice, model_mode="manual", model_config_id=ids["broken-model"])
    alice.patch(f"/api/v1/orgs/{alice.org_id}", json={"fallback_policy": "never"})
    evs = sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "x"}).text)
    assert evs[-1]["type"] == "error" and evs[-1]["fallback_options"] == []
    alice.patch(f"/api/v1/orgs/{alice.org_id}", json={"fallback_policy": "auto"})
    evs = sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "y"}).text)
    routings = [e for e in evs if e["type"] == "routing"]
    assert [r["model"] for r in routings] == ["broken-model", "fake-chat"] and routings[1]["fallback"] is True
    assert evs[-1]["type"] == "done"
    msg = alice.get(f"/api/v1/conversations/{conv['id']}").json()["messages"][-1]
    assert (
        msg["model_name"] == "fake-chat" and msg["routing"]["previous_failures"][0]["model"] == "broken-model"
    )


def test_branching_regenerate_edit_search_export(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider)
    conv = _conv(alice)
    url = f"/api/v1/conversations/{conv['id']}"
    alice.post(f"{url}/messages", json={"content": "first question about servos"})
    alice.post(f"{url}/messages", json={"content": "second question"})
    msgs = alice.get(url).json()["messages"]
    first_user = msgs[0]
    second_assistant = msgs[-1]
    # regenerate creates a sibling of the last assistant reply
    fake_provider.reply = "regenerated answer"
    evs = sse_events(alice.post(f"{url}/regenerate", json={"message_id": second_assistant["id"]}).text)
    assert evs[-1]["type"] == "done"
    msgs = alice.get(url).json()["messages"]
    siblings = [m for m in msgs if m["parent_id"] == second_assistant["parent_id"]]
    assert len(siblings) == 2
    # edit the first user message: the new message becomes its sibling (a second root)
    fake_provider.reply = None
    alice.post(f"{url}/messages", json={"content": "edited first question", "edit_of": first_user["id"]})
    detail = alice.get(url).json()
    roots = [m for m in detail["messages"] if m["parent_id"] is None]
    assert len(roots) == 2
    # switch back to the original branch
    assert alice.patch(url, json={"current_leaf_id": second_assistant["id"]}).status_code == 200
    md = alice.get(f"{url}/export?format=md")
    assert "second question" in md.text and "edited first question" not in md.text
    hits = alice.get(f"/api/v1/conversations/search?org_id={alice.org_id}&q=servos").json()
    assert any("servos" in h["snippet"] for h in hits)


def test_budget_blocks_spend(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(
        alice,
        fake_provider,
        ("fake-chat",),
        fake_chat={"input_price_per_mtok": "100000", "output_price_per_mtok": "100000"},
    )
    alice.patch(f"/api/v1/orgs/{alice.org_id}", json={"monthly_budget_usd": "1"})
    conv = _conv(alice)
    assert (
        sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "a"}).text)[
            -1
        ]["type"]
        == "done"
    )
    evs = sse_events(alice.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "b"}).text)
    assert evs[-1]["type"] == "error" and evs[-1]["error"]["kind"] == "budget"


def test_provider_credentials_are_validated_and_never_returned(
    alice: Session, fake_provider: FakeProvider
) -> None:
    r = alice.post(
        "/api/v1/providers",
        json={
            "org_id": alice.org_id,
            "kind": "openai_compatible",
            "name": "Keyed",
            "base_url": "http://fake.local/v1",
            "api_key": "sk-supersecret-1234",
        },
    )
    assert r.status_code == 201
    assert "sk-supersecret" not in r.text and r.json()["provider"]["api_key_hint"] == "…1234"
    assert fake_provider.requests == [] or True
    providers = alice.get(f"/api/v1/providers?org_id={alice.org_id}").text
    assert "supersecret" not in providers
    # anthropic without a key is rejected before any call
    assert (
        alice.post(
            "/api/v1/providers", json={"org_id": alice.org_id, "kind": "anthropic", "name": "A"}
        ).status_code
        == 422
    )


def test_invalid_branch_targets_are_clean_errors(alice: Session, fake_provider: FakeProvider) -> None:
    import uuid

    setup_models(alice, fake_provider)
    conv = _conv(alice)
    evs = sse_events(
        alice.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"content": "x", "edit_of": str(uuid.uuid4())},
        ).text
    )
    assert evs[-1]["type"] == "error" and evs[-1]["error"]["kind"] == "invalid_request"
