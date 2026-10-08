from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from ..deps import DB, CurrentUser, client_ip, rate_limit, require_project
from ..jobs import enqueue
from ..models import GeneratedArtifact
from ..schemas import AiEditIn, BuilderInitIn, FileRenameIn, FileWriteIn, JobCreated, PreviewOut, WorkspaceFileContent, WorkspaceFileOut
from ..services import builder as b
from ..services.artifacts import artifact_dict
from ..services.audit import audit
from ..services.previews import PreviewError, active_preview, preview_url, start_preview, stop_preview
from ..storage import get_storage

router = APIRouter(prefix="/builder", tags=["builder"])


@router.get("/templates")
def templates() -> list[dict[str, Any]]:
    return b.template_catalog()


@router.post("/projects/{project_id}/init", response_model=list[WorkspaceFileOut])
def init(project_id: uuid.UUID, body: BuilderInitIn, user: CurrentUser, db: DB) -> list[WorkspaceFileOut]:
    p, _ = require_project(db, user, project_id, "editor")
    if b.workspace_rows(db, p.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Workspace already has files")
    try:
        b.init_from_template(db, p, body.template, user.id)
    except b.WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return [WorkspaceFileOut.model_validate(r, from_attributes=True) for r in b.workspace_rows(db, p.id)]


@router.get("/projects/{project_id}/files", response_model=list[WorkspaceFileOut])
def list_files(project_id: uuid.UUID, user: CurrentUser, db: DB) -> list[WorkspaceFileOut]:
    p, _ = require_project(db, user, project_id)
    return [WorkspaceFileOut.model_validate(r, from_attributes=True) for r in b.workspace_rows(db, p.id)]


@router.get("/projects/{project_id}/file", response_model=WorkspaceFileContent)
def read_file(project_id: uuid.UUID, path: str, user: CurrentUser, db: DB) -> WorkspaceFileContent:
    p, _ = require_project(db, user, project_id)
    try:
        norm = b.normalize_path(path)
    except b.WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = next((r for r in b.workspace_rows(db, p.id) if r.path == norm), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    data = get_storage().get_bytes(row.storage_key)
    try:
        content, binary = data.decode("utf-8"), False
    except UnicodeDecodeError:
        content, binary = None, True
    return WorkspaceFileContent(path=row.path, size_bytes=row.size_bytes, sha256=row.sha256, mime_type=row.mime_type, updated_at=row.updated_at, content=content, binary=binary)


@router.put("/projects/{project_id}/file", response_model=WorkspaceFileOut, dependencies=[rate_limit("builder-write", 300)])
def write_file(project_id: uuid.UUID, body: FileWriteIn, user: CurrentUser, db: DB) -> WorkspaceFileOut:
    p, _ = require_project(db, user, project_id, "editor")
    try:
        row = b.write_file(db, p, body.path, body.content.encode(), user.id)
    except b.WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    return WorkspaceFileOut.model_validate(row, from_attributes=True)


@router.post("/projects/{project_id}/rename", response_model=WorkspaceFileOut)
def rename_file(project_id: uuid.UUID, body: FileRenameIn, user: CurrentUser, db: DB) -> WorkspaceFileOut:
    p, _ = require_project(db, user, project_id, "editor")
    try:
        src, dst = b.normalize_path(body.path), b.normalize_path(body.new_path)
    except b.WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    rows = {r.path: r for r in b.workspace_rows(db, p.id)}
    if src not in rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if dst in rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "Destination exists")
    rows[src].path = dst
    db.flush()
    return WorkspaceFileOut.model_validate(rows[src], from_attributes=True)


@router.delete("/projects/{project_id}/file", status_code=204)
def delete_file(project_id: uuid.UUID, path: str, user: CurrentUser, db: DB) -> None:
    p, _ = require_project(db, user, project_id, "editor")
    try:
        if not b.delete_file(db, p, path):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    except b.WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/projects/{project_id}/test", response_model=JobCreated, status_code=202)
def run_tests(project_id: uuid.UUID, user: CurrentUser, db: DB) -> JobCreated:
    p, _ = require_project(db, user, project_id, "editor")
    job = enqueue(db, "builder.test", org_id=p.org_id, user_id=user.id, project_id=p.id, max_attempts=1)
    return JobCreated(job_id=job.id)


@router.post("/projects/{project_id}/ai-edit", response_model=JobCreated, status_code=202, dependencies=[rate_limit("ai-edit", 20)])
def ai_edit(project_id: uuid.UUID, body: AiEditIn, request: Request, user: CurrentUser, db: DB) -> JobCreated:
    p, _ = require_project(db, user, project_id, "editor")
    if not p.builder_template:
        raise HTTPException(status.HTTP_409_CONFLICT, "Initialise the project from a Builder template first")
    job = enqueue(db, "builder.ai_edit", org_id=p.org_id, user_id=user.id, project_id=p.id, payload={"prompt": body.prompt}, max_attempts=1)
    audit(db, "builder.ai_edit", user_id=user.id, org_id=p.org_id, target_type="project", target_id=p.id, ip=client_ip(request))
    return JobCreated(job_id=job.id)


@router.get("/projects/{project_id}/snapshots")
def snapshots(project_id: uuid.UUID, user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    p, _ = require_project(db, user, project_id)
    rows = db.scalars(select(GeneratedArtifact).where(GeneratedArtifact.project_id == p.id, GeneratedArtifact.kind == "code").order_by(GeneratedArtifact.created_at.desc()).limit(50))
    return [artifact_dict(a, include_versions=False) for a in rows]


@router.post("/projects/{project_id}/snapshots", status_code=201)
def create_snapshot(project_id: uuid.UUID, user: CurrentUser, db: DB, label: str = "manual snapshot") -> dict[str, str]:
    p, _ = require_project(db, user, project_id, "editor")
    return {"artifact_id": str(b.snapshot(db, p, user.id, label[:80]))}


@router.post("/projects/{project_id}/snapshots/{artifact_id}/restore")
def restore(project_id: uuid.UUID, artifact_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, int]:
    p, _ = require_project(db, user, project_id, "editor")
    b.snapshot(db, p, user.id, "before restore")
    try:
        n = b.restore_snapshot(db, p, artifact_id, user.id)
    except b.WorkspaceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"files": n}


@router.get("/projects/{project_id}/export")
def export(project_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    p, _ = require_project(db, user, project_id)
    name = re.sub(r"[^A-Za-z0-9]+", "-", p.name).strip("-") or "project"
    data = b.export_zip(b.read_files(db, p.id), root=name)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


@router.post("/projects/{project_id}/preview", response_model=PreviewOut)
def preview(project_id: uuid.UUID, user: CurrentUser, db: DB) -> PreviewOut:
    p, _ = require_project(db, user, project_id, "editor")
    try:
        pv, reused = start_preview(db, p, user.id)
    except PreviewError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PreviewOut(id=pv.id, url=preview_url(pv.id), status=pv.status, reused=reused, started_at=pv.started_at)


@router.get("/projects/{project_id}/preview", response_model=PreviewOut | None)
def preview_status(project_id: uuid.UUID, user: CurrentUser, db: DB) -> PreviewOut | None:
    p, _ = require_project(db, user, project_id)
    pv = active_preview(db, p.id)
    return PreviewOut(id=pv.id, url=preview_url(pv.id), status=pv.status, reused=True, started_at=pv.started_at) if pv else None


@router.delete("/projects/{project_id}/preview", status_code=204)
def stop(project_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    p, _ = require_project(db, user, project_id, "editor")
    pv = active_preview(db, p.id)
    if pv:
        stop_preview(db, pv)
