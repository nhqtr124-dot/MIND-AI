"""Preview proxy, served on its own origin (default http://localhost:8100).

Generated apps are untrusted. Serving them from a different origin than the
MIND web app and API means their JavaScript cannot read MIND cookies or call
the API with the user's credentials. Access needs a signed, expiring token in
the URL path; the upstream is a sandbox container on an internal network.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from .config import get_settings
from .db import session_scope, utcnow
from .models import Preview
from .security import verify_payload

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length", "content-encoding"}

app = FastAPI(title="MIND preview proxy", docs_url=None, redoc_url=None, openapi_url=None)
_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)


def _lookup(token: str) -> tuple[str | None, str]:
    body = verify_payload("preview", token)
    if body is None:
        return None, "Preview link is invalid or expired. Reopen the preview from MIND Builder."
    with session_scope() as db:
        p = db.get(Preview, uuid.UUID(body["id"]))
        if p is None or p.status != "running":
            return None, "This preview has stopped. Start it again from MIND Builder."
        if utcnow() - p.last_access_at > timedelta(seconds=30):
            p.last_access_at = utcnow()
        return p.upstream_url, ""


@app.get("/p/{token}")
async def no_slash(token: str) -> RedirectResponse:
    return RedirectResponse(f"/p/{token}/", status_code=307)


@app.api_route("/p/{token}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(token: str, path: str, request: Request) -> Response:
    upstream, err = _lookup(token)
    if upstream is None:
        return PlainTextResponse(err, status_code=410)
    url = f"{upstream}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP and k.lower() != "cookie"}
    try:
        r = await _client.request(request.method, url, params=request.query_params, content=await request.body(), headers=headers)
    except httpx.HTTPError as exc:
        return PlainTextResponse(f"Preview app is not responding: {type(exc).__name__}", status_code=502)
    out = {k: v for k, v in r.headers.items() if k.lower() not in HOP and k.lower() != "set-cookie"}
    if "location" in out and out["location"].startswith("/"):
        out["location"] = f"/p/{token}{out['location']}"
    ancestors = " ".join(get_settings().cors_origins)
    out["Content-Security-Policy"] = f"frame-ancestors 'self' {ancestors}"
    out["X-Content-Type-Options"] = "nosniff"
    return Response(content=r.content, status_code=r.status_code, headers=out)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
