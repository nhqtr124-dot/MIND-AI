"""Acceptance 9, 10, 11: runnable generated apps in isolated previews (real Docker).

The model is FakeProvider returning scripted edits; the sandbox, tests,
containers and preview proxy are real.
"""

import io
import json
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient
from mind_api.preview_app import app as preview_app
from mind_sandbox import docker_available

from .conftest import FakeProvider, Session, jobs, setup_models

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")


def _edit(title: str, extra_test: bool = True, broken: bool = False) -> str:
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title><link rel="stylesheet" href="styles.css"></head>
<body><main class="card"><h1 id="title">{title}</h1><ul id="todo-list"></ul></main><script src="app.js"></script></body></html>"""
    files = [{"path": "index.html", "content": html}]
    if extra_test:
        expected = "WRONG TITLE" if broken else title
        files.append(
            {
                "path": "tests/test_title.py",
                "content": f"from pathlib import Path\n\ndef test_title():\n    assert '<h1 id=\"title\">{expected}</h1>' in (Path(__file__).parent.parent / 'index.html').read_text()\n",
            }
        )
    return "```json\n" + json.dumps({"summary": f"Set the title to {title}", "files": files}) + "\n```"


def _preview_get(url: str) -> tuple[int, str]:
    path = url.split("preview.test", 1)[1]
    with TestClient(preview_app) as pc:
        r = pc.get(path)
        return r.status_code, r.text


def test_generate_run_preview_and_follow_up_edit(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider)
    proj = alice.post(
        "/api/v1/projects",
        json={"org_id": alice.org_id, "name": "Robotics Dashboard", "builder_template": "static-web"},
    ).json()
    pid = proj["id"]
    # 9: request an app change -> runnable source with passing tests
    fake_provider.reply = _edit("Robotics Competition Dashboard")
    job = alice.post(
        f"/api/v1/builder/projects/{pid}/ai-edit",
        json={"prompt": "Make this a robotics competition dashboard"},
    ).json()
    jobs()
    res = alice.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert res["status"] == "completed", res
    assert res["result"]["tests"]["passed"] and res["result"]["tests"]["ran"]
    assert "2 passed" in res["result"]["tests"]["stdout"]
    files = {f["path"] for f in alice.get(f"/api/v1/builder/projects/{pid}/files").json()}
    assert {"index.html", "tests/test_title.py", "app.js"} <= files
    z = zipfile.ZipFile(io.BytesIO(alice.get(f"/api/v1/builder/projects/{pid}/export").content))
    assert "Robotics-Dashboard/index.html" in z.namelist()

    # 10: live preview in an isolated container, reached through the preview origin
    pv = alice.post(f"/api/v1/builder/projects/{pid}/preview").json()
    try:
        assert pv["url"].startswith("http://preview.test/p/") and not pv["reused"]
        code, body = _preview_get(pv["url"])
        assert code == 200 and "Robotics Competition Dashboard" in body
        code, css = _preview_get(pv["url"] + "styles.css")
        assert code == 200 and ".card" in css
        from mind_api.db import session_scope
        from mind_api.models import Preview

        with session_scope() as db:
            cid = db.get(Preview, pv["id"]).container_id
        net = subprocess.run(
            ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", cid],
            capture_output=True,
            text=True,
        ).stdout
        assert "mind-sandbox-net" in net
        internal = subprocess.run(
            ["docker", "network", "inspect", "-f", "{{.Internal}}", "mind-sandbox-net"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert internal == "true"
        user = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.Config.User}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.Memory}}",
                cid,
            ],
            capture_output=True,
            text=True,
        ).stdout.split()
        assert user[0] == "10001:10001" and user[1] == "true" and int(user[2]) > 0
        # same files -> preview reused
        assert alice.post(f"/api/v1/builder/projects/{pid}/preview").json()["reused"]

        # 11: follow-up prompt changes the app; preview restarts with the new version
        fake_provider.reply = _edit("Robotics Dashboard v2")
        job = alice.post(f"/api/v1/builder/projects/{pid}/ai-edit", json={"prompt": "Rename to v2"}).json()
        jobs()
        assert alice.get(f"/api/v1/jobs/{job['job_id']}").json()["status"] == "completed"
        pv2 = alice.post(f"/api/v1/builder/projects/{pid}/preview").json()
        assert not pv2["reused"]
        code, body = _preview_get(pv2["url"])
        assert "Robotics Dashboard v2" in body
        assert _preview_get(pv["url"])[0] == 410, "old preview must be stopped"
        assert _preview_get("http://preview.test/p/forged.token/")[0] == 410
    finally:
        alice.delete(f"/api/v1/builder/projects/{pid}/preview")
    # undo: restore the snapshot taken before the second edit
    snaps = alice.get(f"/api/v1/builder/projects/{pid}/snapshots").json()
    before_v2 = next(s for s in snaps if s["title"].endswith("before AI edit"))
    assert (
        alice.post(f"/api/v1/builder/projects/{pid}/snapshots/{before_v2['id']}/restore").status_code == 200
    )
    content = alice.get(f"/api/v1/builder/projects/{pid}/file?path=index.html").json()["content"]
    assert "Robotics Competition Dashboard" in content


def test_failing_change_is_repaired_or_reported(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider)
    proj = alice.post(
        "/api/v1/projects", json={"org_id": alice.org_id, "name": "P", "builder_template": "static-web"}
    ).json()
    replies = iter([_edit("Alpha", broken=True), _edit("Alpha")])
    fake_provider.reply = lambda body: next(replies)
    job = alice.post(f"/api/v1/builder/projects/{proj['id']}/ai-edit", json={"prompt": "title Alpha"}).json()
    jobs()
    res = alice.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert res["status"] == "completed" and len(res["result"]["attempts"]) == 2
    assert (
        res["result"]["attempts"][0]["tests_passed"] is False
        and res["result"]["attempts"][1]["tests_passed"] is True
    )
    assert (
        "test failed" in fake_provider.requests[-1]["body"]["messages"][-1]["content"].lower()
        or "failed" in fake_provider.requests[-1]["body"]["messages"][-1]["content"]
    )
    # always-broken output ends as partially completed, never as success
    fake_provider.reply = _edit("Beta", broken=True)
    job = alice.post(f"/api/v1/builder/projects/{proj['id']}/ai-edit", json={"prompt": "title Beta"}).json()
    jobs()
    res = alice.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert res["status"] == "partially_completed" and res["result"]["tests"]["passed"] is False


def test_unsafe_model_output_rejected(alice: Session, fake_provider: FakeProvider) -> None:
    setup_models(alice, fake_provider)
    proj = alice.post(
        "/api/v1/projects", json={"org_id": alice.org_id, "name": "P", "builder_template": "static-web"}
    ).json()
    fake_provider.reply = json.dumps({"summary": "x", "files": [{"path": "../../etc/evil", "content": "x"}]})
    job = alice.post(f"/api/v1/builder/projects/{proj['id']}/ai-edit", json={"prompt": "do it"}).json()
    jobs()
    res = alice.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert res["status"] == "failed" and "applicable" in res["error"]["message"]


def test_workspace_file_crud_and_tests(alice: Session) -> None:
    proj = alice.post(
        "/api/v1/projects", json={"org_id": alice.org_id, "name": "API", "builder_template": "python-fastapi"}
    ).json()
    pid = proj["id"]
    assert (
        alice.put(
            f"/api/v1/builder/projects/{pid}/file", json={"path": "notes/readme.md", "content": "# hi"}
        ).status_code
        == 200
    )
    assert (
        alice.put(
            f"/api/v1/builder/projects/{pid}/file", json={"path": "/abs/../../x", "content": "x"}
        ).status_code
        == 422
    )
    assert (
        alice.put(
            f"/api/v1/builder/projects/{pid}/file", json={"path": ".env", "content": "SECRET=1"}
        ).status_code
        == 422
    )
    assert (
        alice.post(
            f"/api/v1/builder/projects/{pid}/rename",
            json={"path": "notes/readme.md", "new_path": "README.md"},
        ).status_code
        == 200
    )
    assert alice.get(f"/api/v1/builder/projects/{pid}/file?path=README.md").json()["content"] == "# hi"
    job = alice.post(f"/api/v1/builder/projects/{pid}/test").json()
    jobs()
    res = alice.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert res["status"] == "completed" and res["result"]["tests"]["passed"], res
    # break the app -> the test job reports failure
    alice.put(
        f"/api/v1/builder/projects/{pid}/file",
        json={"path": "main.py", "content": "raise SystemExit('broken')\n"},
    )
    job = alice.post(f"/api/v1/builder/projects/{pid}/test").json()
    jobs()
    assert alice.get(f"/api/v1/jobs/{job['job_id']}").json()["status"] == "failed"
