"""Acceptance 12, 13, 15 plus agent planning, verification, workflows and memory isolation."""

import json
from typing import Any

from fastapi.testclient import TestClient

from .conftest import FakeProvider, Session, jobs, register, setup_models


def _run(s: Session, **body: Any) -> dict[str, Any]:
    r = s.post("/api/v1/agents/runs", json={"org_id": s.org_id, **body})
    assert r.status_code == 202, r.text
    jobs()
    return s.get(f"/api/v1/agents/runs/{r.json()['run_id']}").json()


def test_agent_runs_tool_and_records_execution(alice: Session) -> None:
    plan = {"tasks": [
        {"key": "bracket", "tool": "cad.generate_part", "title": "Make bracket", "args": {"template": "l_bracket", "params": {"leg_a": 50}, "title": "Bracket"}},
        {"key": "report", "tool": "documents.generate", "title": "Write report", "depends_on": ["bracket"],
         "args": {"format": "md", "title": "Report", "spec": {"title": "Bracket report", "blocks": [{"type": "paragraph", "text": "Artifact {{bracket.artifact_id}} status {{bracket.validation_status}}."}]}}},
    ]}  # fmt: skip
    run = _run(alice, goal="Bracket plus report", plan=plan)
    assert run["status"] == "completed", run
    tasks = {t["key"]: t for t in run["tasks"]}
    assert tasks["bracket"]["agent_title"] == "CAD Agent" and tasks["bracket"]["verification"]["passed"]
    assert tasks["report"]["verification"]["agent"] == "Verification Agent"
    execs = alice.get(f"/api/v1/agents/runs/{run['id']}/executions").json()
    assert [e["tool"] for e in execs] == ["cad.generate_part", "documents.generate"] and all(
        e["status"] == "succeeded" and e["duration_ms"] >= 0 for e in execs
    )
    assert len(run["result"]["artifacts"]) == 2
    # the dependency output was substituted into the document
    art = alice.get(f"/api/v1/artifacts/{run['result']['artifacts'][1]}").json()
    md = alice.get(f"/api/v1/artifacts/{art['id']}/files/{art['versions'][0]['files'][0]['name']}").text
    assert tasks["bracket"]["output"]["artifact_id"] in md and "print_ready_checks_passed" in md


def test_dangerous_operation_requires_approval(client: TestClient, alice: Session) -> None:
    proj = alice.post(
        "/api/v1/projects", json={"org_id": alice.org_id, "name": "App", "builder_template": "static-web"}
    ).json()
    plan = {
        "tasks": [
            {
                "key": "rm",
                "tool": "files.delete",
                "title": "Delete stylesheet",
                "args": {"path": "styles.css"},
            }
        ]
    }
    r = alice.post("/api/v1/agents/runs", json={"project_id": proj["id"], "goal": "remove css", "plan": plan})
    jobs()
    run = alice.get(f"/api/v1/agents/runs/{r.json()['run_id']}").json()
    assert run["status"] == "awaiting_approval" and run["tasks"][0]["status"] == "awaiting_approval"
    files = [f["path"] for f in alice.get(f"/api/v1/builder/projects/{proj['id']}/files").json()]
    assert "styles.css" in files, "nothing may be deleted before approval"
    approvals = alice.get(f"/api/v1/approvals?org_id={alice.org_id}").json()
    assert len(approvals) == 1 and approvals[0]["risk"] == "high" and approvals[0]["action"] == "files.delete"
    # a viewer in the org cannot approve high-risk actions
    inv = alice.post(
        f"/api/v1/orgs/{alice.org_id}/invitations", json={"email": "v@example.com", "role": "viewer"}
    ).json()
    viewer = register(client, "v@example.com", "Viewer", invitation_token=inv["token"])
    assert (
        viewer.post(f"/api/v1/approvals/{approvals[0]['id']}", json={"decision": "approve"}).status_code
        == 403
    )
    assert (
        alice.post(
            f"/api/v1/approvals/{approvals[0]['id']}", json={"decision": "approve", "note": "ok"}
        ).status_code
        == 200
    )
    jobs()
    run = alice.get(f"/api/v1/agents/runs/{run['id']}").json()
    assert run["status"] == "completed"
    assert "styles.css" not in [
        f["path"] for f in alice.get(f"/api/v1/builder/projects/{proj['id']}/files").json()
    ]
    assert {"approval.requested", "approval.approved"} <= {
        a["action"] for a in alice.get(f"/api/v1/admin/audit?org_id={alice.org_id}").json()
    }


def test_rejected_approval_cancels_task_and_dependents(alice: Session) -> None:
    plan = {"tasks": [
        {"key": "mem", "tool": "memory.save", "args": {"content": "the team uses PETG", "scope": "team"}},
        {"key": "after", "tool": "llm.generate", "args": {"prompt": "x"}, "depends_on": ["mem"]},
    ]}  # fmt: skip
    r = alice.post("/api/v1/agents/runs", json={"org_id": alice.org_id, "goal": "remember", "plan": plan})
    jobs()
    appr = alice.get(f"/api/v1/approvals?org_id={alice.org_id}").json()[0]
    assert appr["risk"] == "medium"
    alice.post(f"/api/v1/approvals/{appr['id']}", json={"decision": "reject"})
    jobs()
    run = alice.get(f"/api/v1/agents/runs/{r.json()['run_id']}").json()
    assert run["status"] == "failed" and {t["status"] for t in run["tasks"]} == {"cancelled"}
    assert alice.get(f"/api/v1/memory?org_id={alice.org_id}").json() == []


def test_failed_verification_gives_partial_completion(alice: Session) -> None:
    plan = {"tasks": [
        {"key": "ok", "tool": "cad.generate_part", "args": {"template": "standoff"}},
        {"key": "bad", "tool": "code.run", "args": {"runtime": "python", "command": ["python", "-c", "import sys; sys.exit(2)"], "use_project_workspace": False}},
    ]}  # fmt: skip
    run = _run(alice, goal="mixed", plan=plan)
    tasks = {t["key"]: t for t in run["tasks"]}
    assert tasks["ok"]["status"] == "completed"
    assert (
        tasks["bad"]["status"] == "failed"
        and "verification failed" in tasks["bad"]["error"]
        and tasks["bad"]["output"]["exit_code"] == 2
    )
    assert run["status"] == "partially_completed"


def test_invalid_plans_rejected(alice: Session) -> None:
    for plan in (
        {"tasks": [{"key": "a", "tool": "rm.rf", "args": {}}]},
        {
            "tasks": [
                {"key": "a", "tool": "llm.generate", "args": {"prompt": "x"}, "depends_on": ["b"]},
                {"key": "b", "tool": "llm.generate", "args": {"prompt": "y"}, "depends_on": ["a"]},
            ]
        },
        {"tasks": [{"key": "a", "tool": "cad.generate_part", "args": {"params": {}}}]},
    ):
        assert (
            alice.post(
                "/api/v1/agents/runs", json={"org_id": alice.org_id, "goal": "x", "plan": plan}
            ).status_code
            == 422
        )


def test_llm_planned_run(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider)
    plan = {
        "tasks": [
            {
                "key": "part",
                "tool": "cad.generate_part",
                "title": "Standoff",
                "args": {"template": "standoff", "params": {"height": 15}},
            }
        ]
    }
    fake_provider.reply = "Here is the plan:\n```json\n" + json.dumps(plan) + "\n```"
    run = _run(alice, goal="Make me a 15 mm spacer")
    assert run["plan_source"] == "llm" and run["status"] == "completed"
    assert run["tasks"][0]["output"]["validation_status"] in (
        "print_ready_checks_passed",
        "printable_with_warnings",
    )
    # planner prompt lists the tools and never the unavailable ones as available
    assert "cad.generate_part" in fake_provider.requests[0]["body"]["messages"][-1]["content"]


def test_llm_planning_without_model_fails_honestly(alice: Session) -> None:
    run = _run(alice, goal="do something")
    assert run["status"] == "failed" and "No enabled chat model" in run["error"]


def test_cancel_run_waiting_for_approval(alice: Session) -> None:
    plan = {"tasks": [{"key": "m", "tool": "memory.save", "args": {"content": "x"}}]}
    r = alice.post("/api/v1/agents/runs", json={"org_id": alice.org_id, "goal": "x", "plan": plan})
    jobs()
    out = alice.post(f"/api/v1/agents/runs/{r.json()['run_id']}/cancel").json()
    assert out["status"] == "cancelled"
    assert alice.get(f"/api/v1/approvals?org_id={alice.org_id}").json() == []


def test_workflow_manual_and_scheduled(alice: Session) -> None:
    from datetime import UTC, datetime, timedelta

    from mind_api.db import session_scope
    from mind_api.models import ScheduledJob
    from mind_api.services.scheduler import enqueue_due_workflows

    definition = {"tasks": [{"key": "p", "tool": "cad.generate_part", "args": {"template": "standoff"}}]}
    wf = alice.post(
        "/api/v1/workflows",
        json={"org_id": alice.org_id, "name": "Nightly part", "definition": definition, "cron": "0 3 * * *"},
    ).json()
    assert wf["next_run_at"] is not None
    r = alice.post(f"/api/v1/workflows/{wf['id']}/run").json()
    jobs()
    assert alice.get(f"/api/v1/agents/runs/{r['run_id']}").json()["status"] == "completed"
    with session_scope() as db:
        sj = db.query(ScheduledJob).one()
        sj.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    with session_scope() as db:
        assert enqueue_due_workflows(db) == 1
    jobs()
    runs = alice.get(f"/api/v1/agents/runs?org_id={alice.org_id}").json()
    assert len(runs) == 2 and all(r["status"] == "completed" for r in runs)
    assert (
        alice.post(
            "/api/v1/workflows",
            json={"org_id": alice.org_id, "name": "bad", "definition": definition, "cron": "not a cron"},
        ).status_code
        == 422
    )


def test_memory_delete_removes_from_retrieval(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider, ("fake-chat", "fake-embedding"))
    m1 = alice.post(
        "/api/v1/memory",
        json={"org_id": alice.org_id, "content": "Our robot uses a Raspberry Pi 5 controller"},
    ).json()
    alice.post("/api/v1/memory", json={"org_id": alice.org_id, "content": "Team meetings are on Thursdays"})
    jobs()  # embeddings
    hits = alice.get(f"/api/v1/memory/search?org_id={alice.org_id}&q=raspberry controller").json()
    assert hits and hits[0]["id"] == m1["id"]
    assert "vector" in hits[0]["method"] and "fulltext" in hits[0]["method"]
    # memory is injected into chat context
    conv = alice.post("/api/v1/conversations", json={"org_id": alice.org_id}).json()
    alice.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"content": "Which raspberry controller do we use?", "stream": False},
    )
    assert "Raspberry Pi 5" in fake_provider.requests[-1]["body"]["messages"][0]["content"]
    assert alice.delete(f"/api/v1/memory/{m1['id']}").status_code == 204
    hits = alice.get(f"/api/v1/memory/search?org_id={alice.org_id}&q=raspberry controller").json()
    assert m1["id"] not in [h["id"] for h in hits]
    from mind_api.db import session_scope
    from mind_api.models import MemoryEmbedding

    with session_scope() as db:
        assert db.query(MemoryEmbedding).count() == 1  # only the remaining entry's embedding
    alice.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"content": "Which raspberry controller do we use?", "stream": False},
    )
    assert "Raspberry Pi 5" not in fake_provider.requests[-1]["body"]["messages"][0]["content"]


def test_memory_scopes(client: TestClient, alice: Session) -> None:
    team = alice.post("/api/v1/orgs", json={"name": "T"}).json()
    inv = alice.post(
        f"/api/v1/orgs/{team['id']}/invitations", json={"email": "m@example.com", "role": "editor"}
    ).json()
    mia = register(client, "m@example.com", "Mia", invitation_token=inv["token"])
    alice.post(
        "/api/v1/memory",
        json={"org_id": team["id"], "content": "alice personal preference metric units", "scope": "user"},
    )
    alice.post(
        "/api/v1/memory",
        json={"org_id": team["id"], "content": "team standard metric fasteners", "scope": "team"},
    )
    seen = [m["content"] for m in mia.get(f"/api/v1/memory?org_id={team['id']}").json()]
    assert seen == ["team standard metric fasteners"]
    assert [h["content"] for h in mia.get(f"/api/v1/memory/search?org_id={team['id']}&q=metric").json()] == [
        "team standard metric fasteners"
    ]
    export = alice.get(f"/api/v1/memory/export?org_id={team['id']}").json()
    assert len(export["entries"]) == 2
