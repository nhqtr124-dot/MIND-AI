from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from ..db import utcnow
from ..deps import DB, CurrentUser, client_ip, project_role, require_org, require_project
from ..models import Membership, Project, ProjectMember
from ..schemas import ProjectCreate, ProjectOut, ProjectShare, ProjectUpdate, ProjectWithRole
from ..services.audit import audit
from ..services.builder import WorkspaceError, init_from_template

router = APIRouter(prefix="/projects", tags=["projects"])


def _with_role(p: Project, role: str) -> ProjectWithRole:
    return ProjectWithRole.model_validate({**ProjectOut.model_validate(p).model_dump(), "role": role})


@router.get("", response_model=list[ProjectWithRole])
def list_projects(org_id: uuid.UUID, user: CurrentUser, db: DB) -> list[ProjectWithRole]:
    require_org(db, user, org_id)
    out = []
    for p in db.scalars(select(Project).where(Project.org_id == org_id, Project.archived_at.is_(None)).order_by(Project.updated_at.desc())):
        role = project_role(db, user, p)
        if role:
            out.append(_with_role(p, role))
    return out


@router.post("", response_model=ProjectWithRole, status_code=201)
def create_project(body: ProjectCreate, request: Request, user: CurrentUser, db: DB) -> ProjectWithRole:
    _, role = require_org(db, user, body.org_id, "editor")
    p = Project(org_id=body.org_id, name=body.name.strip(), description=body.description, visibility=body.visibility, created_by=user.id)
    db.add(p)
    db.flush()
    if body.visibility == "private":
        db.add(ProjectMember(project_id=p.id, user_id=user.id, role="owner"))
    if body.builder_template:
        try:
            init_from_template(db, p, body.builder_template, user.id)
        except WorkspaceError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    audit(db, "project.create", user_id=user.id, org_id=body.org_id, target_type="project", target_id=p.id, ip=client_ip(request))
    return _with_role(p, "owner" if body.visibility == "private" else role)


@router.get("/{project_id}", response_model=ProjectWithRole)
def get_project(project_id: uuid.UUID, user: CurrentUser, db: DB) -> ProjectWithRole:
    p, role = require_project(db, user, project_id)
    return _with_role(p, role)


@router.patch("/{project_id}", response_model=ProjectWithRole)
def update_project(project_id: uuid.UUID, body: ProjectUpdate, user: CurrentUser, db: DB) -> ProjectWithRole:
    p, role = require_project(db, user, project_id, "editor")
    if body.visibility is not None and body.visibility != p.visibility:
        if role not in ("owner", "admin") and p.created_by != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the project creator or an org admin can change visibility")
        if body.visibility == "private" and db.get(ProjectMember, (p.id, user.id)) is None:
            db.add(ProjectMember(project_id=p.id, user_id=user.id, role="owner"))
        p.visibility = body.visibility
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description
    p.updated_at = utcnow()
    audit(db, "project.update", user_id=user.id, org_id=p.org_id, target_type="project", target_id=p.id, changes=body.model_dump(exclude_none=True))
    return _with_role(p, role)


@router.delete("/{project_id}", status_code=204)
def archive_project(project_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> None:
    p, role = require_project(db, user, project_id, "editor")
    if role not in ("owner", "admin") and p.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the project creator or an org admin can archive a project")
    p.archived_at = utcnow()
    audit(db, "project.archive", user_id=user.id, org_id=p.org_id, target_type="project", target_id=p.id, ip=client_ip(request))


@router.get("/{project_id}/members", response_model=list[ProjectShare])
def project_members(project_id: uuid.UUID, user: CurrentUser, db: DB) -> list[ProjectShare]:
    p, _ = require_project(db, user, project_id)
    return [ProjectShare(user_id=m.user_id, role=m.role) for m in db.scalars(select(ProjectMember).where(ProjectMember.project_id == p.id)) if m.role != "owner"]  # type: ignore[arg-type]


@router.post("/{project_id}/members", status_code=204)
def share_project(project_id: uuid.UUID, body: ProjectShare, user: CurrentUser, db: DB) -> None:
    p, role = require_project(db, user, project_id, "editor")
    if p.visibility != "private":
        raise HTTPException(status.HTTP_409_CONFLICT, "Project is visible to the whole organization; sharing applies to private projects")
    if not db.scalar(select(Membership.id).where(Membership.org_id == p.org_id, Membership.user_id == body.user_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That user is not a member of this organization")
    pm = db.get(ProjectMember, (p.id, body.user_id))
    if pm is None:
        db.add(ProjectMember(project_id=p.id, user_id=body.user_id, role=body.role))
    elif pm.role != "owner":
        pm.role = body.role
    audit(db, "project.share", user_id=user.id, org_id=p.org_id, target_type="project", target_id=p.id, member=str(body.user_id), role=body.role)


@router.delete("/{project_id}/members/{member_id}", status_code=204)
def unshare_project(project_id: uuid.UUID, member_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    p, _ = require_project(db, user, project_id, "editor")
    pm = db.get(ProjectMember, (p.id, member_id))
    if pm is None or pm.role == "owner":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")
    db.delete(pm)
    audit(db, "project.unshare", user_id=user.id, org_id=p.org_id, target_type="project", target_id=p.id, member=str(member_id))
