from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from .config import get_settings
from .routers import (
    admin,
    agents,
    artifacts,
    auth,
    builder,
    chat,
    files,
    jobs,
    memory,
    orgs,
    projects,
    providers,
    studios,
)
from .services import register_handlers


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("request_id", "method", "path", "status", "ms"):
            if hasattr(record, k):
                data[k] = getattr(record, k)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def _configure_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
        root.setLevel(logging.INFO)


def create_app() -> FastAPI:
    s = get_settings()
    if s.env != "test":
        _configure_logging()
    register_handlers()
    app = FastAPI(
        title="MIND AI API",
        version="0.1.0",
        description="Unified multimodal AI workspace. All endpoints are versioned under /api/v1.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )
    log = logging.getLogger("mind.http")

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.monotonic()
        try:
            response: Response = await call_next(request)
        except Exception:
            log.exception(
                "unhandled error",
                extra={"request_id": rid, "method": request.method, "path": request.url.path},
            )
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if s.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.monotonic() - start) * 1000, 1),
            },
        )
        return response

    for r in (
        auth,
        orgs,
        projects,
        files,
        artifacts,
        jobs,
        providers,
        chat,
        studios,
        agents,
        memory,
        builder,
        admin,
    ):
        app.include_router(r.router, prefix="/api/v1")
    return app


app = create_app()
