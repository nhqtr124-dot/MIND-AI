from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from mind_docs import libreoffice_available
from mind_sandbox import docker_available
from sqlalchemy import func, select, text

from ..config import get_settings
from ..db import get_engine
from ..deps import DB, CurrentUser, get_rate_limiter, require_org
from ..models import AuditLog, IntegrationCredential, ModelConfig, ModelProvider, UsageEvent, User
from ..schemas import AuditOut, UsageSummary

router = APIRouter(tags=["admin"])


@router.get("/admin/usage", response_model=UsageSummary)
def usage(org_id: uuid.UUID, user: CurrentUser, db: DB, days: int = 30) -> UsageSummary:
    require_org(db, user, org_id, "admin")
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 365)))
    base = (UsageEvent.org_id == org_id, UsageEvent.created_at >= since)
    total = db.scalar(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(*base)) or Decimal(0)
    unpriced = (
        db.scalar(select(func.count()).select_from(UsageEvent).where(*base, UsageEvent.cost_usd.is_(None)))
        or 0
    )

    def grouped(*cols: Any) -> list[dict[str, Any]]:
        q = (
            select(
                *cols,
                func.count().label("events"),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0).label("cost_usd"),
                func.count().filter(UsageEvent.cost_usd.is_(None)).label("unpriced_events"),
            )
            .where(*base)
            .group_by(*cols)
            .order_by(*cols)
        )
        return [
            {
                k: (str(v) if isinstance(v, (Decimal, datetime, uuid.UUID)) else v)
                for k, v in r._mapping.items()
            }
            for r in db.execute(q)
        ]

    day = func.date_trunc("day", UsageEvent.created_at).label("day")
    by_user = grouped(UsageEvent.user_id)
    names = {
        u.id: u.display_name
        for u in db.scalars(
            select(User).where(User.id.in_([uuid.UUID(r["user_id"]) for r in by_user if r["user_id"]]))
        )
    }
    for r in by_user:
        r["display_name"] = names.get(uuid.UUID(r["user_id"])) if r["user_id"] else None
    return UsageSummary(
        org_id=org_id, since=since, total_cost_usd=Decimal(total), unpriced_events=unpriced,
        by_day=grouped(day), by_category=grouped(UsageEvent.category), by_model=grouped(UsageEvent.provider_kind, UsageEvent.model_name), by_user=by_user,
    )  # fmt: skip


@router.get("/admin/audit", response_model=list[AuditOut])
def audit_log(
    org_id: uuid.UUID, user: CurrentUser, db: DB, limit: int = 100, action: str | None = None
) -> list[AuditLog]:
    require_org(db, user, org_id, "admin")
    q = select(AuditLog).where(AuditLog.org_id == org_id)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    return list(db.scalars(q.order_by(AuditLog.created_at.desc()).limit(min(limit, 1000))))


@router.get("/health")
def health() -> dict[str, Any]:
    with get_engine().connect() as c:
        c.execute(text("select 1"))
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


@router.get("/capabilities")
def capabilities(user: CurrentUser, db: DB, org_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Feature availability computed from real checks, so the UI never presents an unavailable feature as working."""
    s = get_settings()
    docker = s.sandbox_enabled and docker_available()
    lo = libreoffice_available()
    ffmpeg = shutil.which("ffmpeg") is not None
    models: list[ModelConfig] = []
    providers: list[ModelProvider] = []
    integrations: list[IntegrationCredential] = []
    if org_id:
        require_org(db, user, org_id)
        models = list(
            db.scalars(
                select(ModelConfig).where(ModelConfig.org_id == org_id, ModelConfig.enabled.is_(True))
            ).unique()
        )
        providers = list(db.scalars(select(ModelProvider).where(ModelProvider.org_id == org_id)))
        integrations = list(
            db.scalars(
                select(IntegrationCredential).where(
                    IntegrationCredential.org_id == org_id, IntegrationCredential.enabled.is_(True)
                )
            )
        )
    caps = {c for m in models for c in m.capabilities}
    image_models = [m for m in models if "image_generation" in m.capabilities]

    def f(status: str, detail: str, **extra: Any) -> dict[str, Any]:
        return {"status": status, "detail": detail, **extra}

    chat_ok = "chat" in caps
    return {
        "server": {
            "docker_sandbox": docker,
            "libreoffice": lo,
            "ffmpeg": ffmpeg,
            "storage": s.storage_backend,
            "rate_limiter": get_rate_limiter().backend,
        },
        "providers": [{"name": p.name, "kind": p.kind, "status": p.status} for p in providers],
        "features": {
            "chat": f(
                "available" if chat_ok else "needs_configuration",
                f"Enabled chat models: {sum('chat' in m.capabilities for m in models)}"
                if chat_ok
                else "Add an AI provider and enable a chat model in Settings.",
            ),
            "vision_chat": f(
                "available" if "vision" in caps else "needs_configuration",
                "Requires an enabled model with the 'vision' capability.",
            ),
            "agents": f(
                "available",
                "Explicit plans always run; LLM planning needs a chat model.",
                llm_planning=chat_ok,
            ),
            "documents": f(
                "available",
                "DOCX/PDF/PPTX/XLSX/CSV/MD/HTML/TXT generation and validation run locally.",
                previews=lo,
                conversion=lo,
                ai_outlines=chat_ok,
            ),
            "cad": f(
                "available",
                "Parametric CAD with CadQuery/OpenCascade and mesh validation run locally.",
                text_to_cad=chat_ok,
            ),
            "builder": f(
                "available" if docker else "unavailable",
                "Code execution and live preview run in Docker sandboxes."
                if docker
                else "Docker daemon not reachable: code execution and previews are disabled.",
                ai_edits=chat_ok and docker,
            ),
            "research": f(
                "available" if integrations else "needs_configuration",
                "Web search via configured engine; URL-only research works without one.",
                engines=[i.kind for i in integrations],
                synthesis=chat_ok,
            ),
            "memory": f(
                "available",
                "Full-text retrieval always; semantic retrieval when an embedding model is enabled.",
                semantic="embeddings" in caps,
            ),
            "image_generation": f(
                "available" if image_models else "needs_configuration",
                "Requires an enabled image-generation model (OpenAI-compatible images API).",
                models=[m.display_name for m in image_models],
            ),
            "image_editing": f(
                "available",
                "Local Pillow edits: resize, crop, rotate, flip, mirror, grayscale, brightness, contrast, format conversion.",
            ),
            "video": f(
                "not_implemented",
                "Video generation and editing are on the roadmap (docs/ROADMAP.md).",
                ffmpeg_installed=ffmpeg,
            ),
            "voice": f(
                "not_implemented", "Speech-to-text and text-to-speech are on the roadmap (docs/ROADMAP.md)."
            ),
            "deployment": f(
                "not_implemented", "One-click deployment is not implemented; export the project ZIP instead."
            ),
        },
    }
