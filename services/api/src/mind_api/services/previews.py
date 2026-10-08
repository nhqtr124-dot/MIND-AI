"""Live previews of Builder projects.

Each preview is a sandbox container on the internal network, reachable only via
the preview proxy (a separate origin, see preview_app.py) with a signed,
expiring token in the URL path.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from mind_sandbox import (
    PROJECT_TEMPLATES,
    SandboxError,
    docker_available,
    is_running,
    logs,
    materialize,
    start_service,
    stop,
    wait_until_ready,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Preview, Project
from ..security import sign_payload
from .builder import files_digest, read_files, workspace_rows

log = logging.getLogger("mind.previews")
TOKEN_TTL_S = 4 * 3600


class PreviewError(Exception):
    pass


def preview_url(preview_id: uuid.UUID) -> str:
    token = sign_payload("preview", {"id": str(preview_id)}, TOKEN_TTL_S)
    return f"{get_settings().preview_public_url.rstrip('/')}/p/{token}/"


def active_preview(db: Session, project_id: uuid.UUID) -> Preview | None:
    return db.scalar(
        select(Preview)
        .where(Preview.project_id == project_id, Preview.status == "running")
        .order_by(Preview.started_at.desc())
    )


def stop_preview(db: Session, p: Preview) -> None:
    stop(p.container_id)
    p.status, p.stopped_at = "stopped", utcnow()


def start_preview(db: Session, project: Project, user_id: uuid.UUID) -> tuple[Preview, bool]:
    """Return (preview, reused). Reuses a running preview if files are unchanged."""
    s = get_settings()
    if not s.sandbox_enabled or not docker_available():
        raise PreviewError("Live preview needs the Docker sandbox, which is not available on this server")
    t = PROJECT_TEMPLATES.get(project.builder_template or "")
    if t is None:
        raise PreviewError("initialise the project from a Builder template before previewing")
    digest = files_digest(workspace_rows(db, project.id))
    current = active_preview(db, project.id)
    if current is not None:
        if current.files_sha == digest and is_running(current.container_id):
            current.last_access_at = utcnow()
            return current, True
        stop_preview(db, current)
    running = (
        db.scalar(
            select(func.count())
            .select_from(Preview)
            .where(Preview.org_id == project.org_id, Preview.status == "running")
        )
        or 0
    )
    if running >= s.max_previews_per_org:
        raise PreviewError(
            f"preview limit reached ({s.max_previews_per_org} running in this organization); stop another preview first"
        )
    try:
        wd = materialize(read_files(db, project.id))
        h = start_service(wd, list(t.preview_command), t.port, runtime=t.runtime)
    except SandboxError as exc:
        raise PreviewError(str(exc)) from exc
    if not wait_until_ready(h, timeout_s=25):
        out = logs(h.container_id)
        stop(h.container_id)
        raise PreviewError(f"the app did not start within 25 s. Container output:\n{out[-3000:]}")
    p = Preview(
        org_id=project.org_id,
        project_id=project.id,
        started_by=user_id,
        container_id=h.container_id,
        upstream_url=h.url,
        files_sha=digest,
    )
    db.add(p)
    db.flush()
    return p, False


def reap_idle_previews() -> int:
    cutoff = utcnow() - timedelta(minutes=get_settings().preview_idle_minutes)
    n = 0
    with session_scope() as db:
        for p in db.scalars(select(Preview).where(Preview.status == "running")):
            if p.last_access_at < cutoff or not is_running(p.container_id):
                stop_preview(db, p)
                n += 1
    if n:
        log.info("stopped %d idle preview(s)", n)
    return n
