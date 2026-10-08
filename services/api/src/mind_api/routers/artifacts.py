from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from ..config import get_settings
from ..deps import DB, CurrentUser, client_ip, require_org, require_project, resolve_scope
from ..models import GeneratedArtifact
from ..schemas import ArtifactMove, ArtifactOut, SignedUrlOut
from ..security import verify_payload
from ..services.artifacts import artifact_dict, download_token
from ..services.audit import audit
from ..storage import StorageError, get_storage
from .files import safe_file_response

router = APIRouter(tags=["artifacts"])


def get_artifact(db: DB, user: CurrentUser, artifact_id: uuid.UUID, min_role: str = "viewer") -> GeneratedArtifact:
    a = db.get(GeneratedArtifact, artifact_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    if a.project_id:
        require_project(db, user, a.project_id, min_role)
    else:
        require_org(db, user, a.org_id, min_role)
    return a


def _file(a: GeneratedArtifact, name: str, version: int | None) -> dict[str, Any]:
    v = next((v for v in a.versions if v.version == (version or a.current_version)), None)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    f = next((f for f in v.files if f["name"] == name), None)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found in this version")
    return f


@router.get("/artifacts", response_model=list[ArtifactOut])
def list_artifacts(
    user: CurrentUser, db: DB, org_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None, kind: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    scope = resolve_scope(db, user, org_id, project_id)
    q = select(GeneratedArtifact).where(GeneratedArtifact.org_id == scope.org_id)
    if scope.project_id:
        q = q.where(GeneratedArtifact.project_id == scope.project_id)
    else:
        # Org-level listing: artifacts without a project plus those in projects the caller can see.
        from ..deps import project_role
        from ..models import Project

        visible = [p.id for p in db.scalars(select(Project).where(Project.org_id == scope.org_id)) if project_role(db, user, p)]
        q = q.where((GeneratedArtifact.project_id.is_(None)) | (GeneratedArtifact.project_id.in_(visible or [uuid.UUID(int=0)])))
    if kind:
        q = q.where(GeneratedArtifact.kind == kind)
    return [artifact_dict(a) for a in db.scalars(q.order_by(GeneratedArtifact.created_at.desc()).limit(min(limit, 200)))]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
def read_artifact(artifact_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    return artifact_dict(get_artifact(db, user, artifact_id))


@router.get("/artifacts/{artifact_id}/files/{name}")
def download_artifact_file(artifact_id: uuid.UUID, name: str, user: CurrentUser, db: DB, version: int | None = None, inline: bool = False) -> Response:
    a = get_artifact(db, user, artifact_id)
    f = _file(a, name, version)
    try:
        data = get_storage().get_bytes(f["storage_key"])
    except StorageError as exc:
        raise HTTPException(status.HTTP_410_GONE, "The stored file is missing") from exc
    return safe_file_response(data, f["name"], f["mime_type"], inline)


@router.post("/artifacts/{artifact_id}/files/{name}/signed-url", response_model=SignedUrlOut)
def signed_url(artifact_id: uuid.UUID, name: str, user: CurrentUser, db: DB, version: int | None = None, ttl_s: int = 3600) -> SignedUrlOut:
    a = get_artifact(db, user, artifact_id)
    f = _file(a, name, version)
    ttl = max(60, min(ttl_s, 7 * 86400))
    tok = download_token(a.org_id, f["storage_key"], f["name"], f["mime_type"], ttl)
    return SignedUrlOut(url=f"{get_settings().public_api_url}/api/v1/downloads/{tok}", expires_in=ttl)


@router.get("/downloads/{token}", include_in_schema=True)
def signed_download(token: str) -> Response:
    body = verify_payload("dl", token)
    if body is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Download link is invalid or expired")
    try:
        data = get_storage().get_bytes(body["k"])
    except StorageError as exc:
        raise HTTPException(status.HTTP_410_GONE, "The stored file is missing") from exc
    return safe_file_response(data, body["n"], body["m"])


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactOut)
def move_artifact(artifact_id: uuid.UUID, body: ArtifactMove, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Attach an artifact to a project (or detach it) inside the same organization."""
    a = get_artifact(db, user, artifact_id, "editor")
    if body.project_id is not None:
        p, _ = require_project(db, user, body.project_id, "editor")
        if p.org_id != a.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    a.project_id = body.project_id
    audit(db, "artifact.move", user_id=user.id, org_id=a.org_id, target_type="artifact", target_id=a.id, project_id=str(body.project_id))
    return artifact_dict(a)


@router.delete("/artifacts/{artifact_id}", status_code=204)
def delete_artifact(artifact_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> None:
    a = get_artifact(db, user, artifact_id, "editor")
    storage = get_storage()
    for v in a.versions:
        for f in v.files:
            storage.delete(f["storage_key"])
    db.delete(a)
    audit(db, "artifact.delete", user_id=user.id, org_id=a.org_id, target_type="artifact", target_id=artifact_id, ip=client_ip(request), title=a.title)
