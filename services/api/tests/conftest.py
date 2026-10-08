"""Test harness.

* Runs against a real PostgreSQL database (``mind_test``) migrated with Alembic.
* Jobs are executed synchronously with ``run_pending()``.
* AI providers are simulated by ``FakeProvider``, an OpenAI-compatible HTTP mock
  plugged into the real adapter through an httpx MockTransport. Tests that use
  it verify MIND's own pipeline (routing, streaming, storage, accounting); they
  do NOT prove that any external provider works.
"""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
from collections.abc import Iterator
from typing import Any

os.environ.setdefault("MIND_ENV", "test")
os.environ.setdefault("MIND_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/mind_test")
os.environ["MIND_STORAGE_DIR"] = tempfile.mkdtemp(prefix="mind-test-storage-")
os.environ["MIND_REDIS_URL"] = ""
os.environ.setdefault("MIND_PREVIEW_PUBLIC_URL", "http://preview.test")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mind_api.db import get_engine  # noqa: E402
from mind_api.deps import get_rate_limiter  # noqa: E402
from mind_api.jobs import run_pending  # noqa: E402
from mind_api.main import app  # noqa: E402
from mind_api.services import providers as provider_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    get_rate_limiter.cache_clear()
    yield
    with get_engine().begin() as c:
        tables = [r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def tiny_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class FakeProvider:
    """OpenAI-compatible mock server. Behaviour is chosen by model name."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.models = ["fake-chat", "fake-vision", "broken-model", "fake-embedding", "gpt-image-fake"]
        self.reply: Any = None  # str or callable(body) -> str

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        body = json.loads(request.content or b"{}")
        self.requests.append({"path": path, "body": body, "headers": dict(request.headers)})
        if path.endswith("/embeddings"):
            vecs = [[float(len(t) % 7), float(sum(map(ord, t)) % 11), 1.0] for t in body["input"]]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)], "usage": {"prompt_tokens": 3}})
        if path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(tiny_png()).decode()} for _ in range(body.get("n", 1))]})
        if body.get("model") == "broken-model":
            return httpx.Response(500, json={"error": {"message": "upstream exploded"}})
        last = body["messages"][-1]["content"]
        last_text = last if isinstance(last, str) else " ".join(p.get("text", "") for p in last if p.get("type") == "text")
        reply = self.reply(body) if callable(self.reply) else self.reply or f"ECHO: {last_text[-60:]}"
        chunks = [reply[i : i + 7] for i in range(0, len(reply), 7)] or [""]
        lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}, 'finish_reason': None}]})}\n\n" for c in chunks]
        lines.append(f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n")
        lines.append(f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': len(chunks)}})}\n\n")
        lines.append("data: [DONE]\n\n")
        return httpx.Response(200, content="".join(lines).encode(), headers={"content-type": "text/event-stream"})


@pytest.fixture
def fake_provider() -> Iterator[FakeProvider]:
    fp = FakeProvider()
    provider_service.HTTP_CLIENT_FACTORY = lambda: httpx.AsyncClient(transport=httpx.MockTransport(fp.handler))
    yield fp
    provider_service.HTTP_CLIENT_FACTORY = None


class Session:
    def __init__(self, client: TestClient, email: str, token: str, user: dict[str, Any]) -> None:
        self.client, self.email, self.token, self.user = client, email, token, user
        self.headers = {"Authorization": f"Bearer {token}"}
        self.org_id = self.get("/api/v1/orgs").json()[0]["id"]

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.post(url, headers=self.headers, **kw)

    def patch(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.patch(url, headers=self.headers, **kw)

    def put(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.put(url, headers=self.headers, **kw)

    def delete(self, url: str, **kw: Any) -> httpx.Response:
        return self.client.delete(url, headers=self.headers, **kw)


def register(client: TestClient, email: str, name: str = "Tester", invitation_token: str | None = None) -> Session:
    body = {"email": email, "password": "correct horse battery", "display_name": name}
    if invitation_token:
        body["invitation_token"] = invitation_token
    r = client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    client.cookies.clear()  # tests use Bearer auth unless they test cookies explicitly
    data = r.json()
    return Session(client, email, data["access_token"], data["user"])


@pytest.fixture
def alice(client: TestClient) -> Session:
    return register(client, "alice@example.com", "Alice")


@pytest.fixture
def bob(client: TestClient) -> Session:
    return register(client, "bob@example.com", "Bob")


def setup_models(s: Session, fp: FakeProvider, enable: tuple[str, ...] = ("fake-chat",), **model_updates: Any) -> dict[str, str]:
    r = s.post("/api/v1/providers", json={"org_id": s.org_id, "kind": "openai_compatible", "name": "Fake", "base_url": "http://fake.local/v1"})
    assert r.status_code == 201, r.text
    assert r.json()["error"] is None and r.json()["discovered"] == len(fp.models)
    ids = {m["model_name"]: m["id"] for m in s.get(f"/api/v1/models?org_id={s.org_id}").json()}
    for name in enable:
        upd: dict[str, Any] = {"enabled": True, **model_updates.get(name.replace("-", "_"), {})}
        r = s.patch(f"/api/v1/models/{ids[name]}", json=upd)
        assert r.status_code == 200, r.text
    return ids


def sse_events(text_body: str) -> list[dict[str, Any]]:
    out = []
    for block in text_body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:]))
    return out


def jobs() -> int:
    return run_pending()
