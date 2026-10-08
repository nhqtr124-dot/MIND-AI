from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from ..db import session_scope
from ..deps import DB, CurrentUser, client_ip, require_org, require_project
from ..jobs import enqueue
from ..models import MemoryEntry
from ..schemas import MemoryCreate, MemoryOut, MemorySearchHit, MemoryUpdate
from ..services import memory_service
from ..services.audit import audit

router = APIRouter(prefix="/memory", tags=["memory"])


def _get(db: DB, user: CurrentUser, memory_id: uuid.UUID, write: bool = False) -> MemoryEntry:
    m = db.get(MemoryEntry, memory_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    visible = db.scalar(select(MemoryEntry.id).where(MemoryEntry.id == memory_id, memory_service.accessible_filter(db, user, m.org_id)))
    if not visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    if write and m.user_id != user.id:
        _, role = require_org(db, user, m.org_id)
        if role not in ("owner", "admin"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author or an org admin can change this memory")
    return m


@router.get("", response_model=list[MemoryOut])
def list_memory(org_id: uuid.UUID, user: CurrentUser, db: DB, scope: str | None = None, project_id: uuid.UUID | None = None, limit: int = 200) -> list[dict[str, Any]]:
    require_org(db, user, org_id)
    q = select(MemoryEntry).where(memory_service.accessible_filter(db, user, org_id))
    if scope:
        q = q.where(MemoryEntry.scope == scope)
    if project_id:
        q = q.where(MemoryEntry.project_id == project_id)
    return [memory_service.memory_dict(m) for m in db.scalars(q.order_by(MemoryEntry.updated_at.desc()).limit(min(limit, 1000)))]


@router.post("", response_model=MemoryOut, status_code=201)
def create_memory(body: MemoryCreate, request: Request, user: CurrentUser, db: DB) -> dict[str, Any]:
    _, role = require_org(db, user, body.org_id, "viewer" if body.scope == "user" else "editor")
    if body.scope == "project":
        if body.project_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "project_id is required for project memory")
        p, _ = require_project(db, user, body.project_id, "editor")
        if p.org_id != body.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    m = MemoryEntry(org_id=body.org_id, user_id=user.id, scope=body.scope, project_id=body.project_id if body.scope == "project" else None, content=body.content, tags=body.tags, source="explicit")
    db.add(m)
    db.flush()
    if memory_service.embedding_model(db, body.org_id) is not None:
        enqueue(db, "memory.embed", org_id=body.org_id, user_id=user.id, payload={"memory_id": str(m.id)})
    audit(db, "memory.create", user_id=user.id, org_id=body.org_id, target_type="memory", target_id=m.id, ip=client_ip(request), scope=body.scope)
    return memory_service.memory_dict(m)


@router.get("/search", response_model=list[MemorySearchHit])
async def search_memory(org_id: uuid.UUID, q: str, user: CurrentUser, db: DB, project_id: uuid.UUID | None = None, k: int = 10) -> list[dict[str, Any]]:
    require_org(db, user, org_id)
    vec, mid = await memory_service.embed_query(session_scope, org_id, q)
    hits = memory_service.search(db, user, org_id, q, project_id=project_id, k=min(k, 50), query_vector=vec, embedding_model_id=mid)
    return [{**memory_service.memory_dict(e), "score": s, "method": method} for e, s, method in hits]


@router.get("/export")
def export_memory(org_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    require_org(db, user, org_id)
    rows = db.scalars(select(MemoryEntry).where(MemoryEntry.org_id == org_id, MemoryEntry.user_id == user.id).order_by(MemoryEntry.created_at))
    return {"org_id": str(org_id), "user_id": str(user.id), "entries": [memory_service.memory_dict(m) for m in rows]}


@router.patch("/{memory_id}", response_model=MemoryOut)
def update_memory(memory_id: uuid.UUID, body: MemoryUpdate, user: CurrentUser, db: DB) -> dict[str, Any]:
    m = _get(db, user, memory_id, write=True)
    if body.content is not None:
        m.content = body.content
        # Stale embeddings would keep matching old content: drop them and re-embed.
        for e in list(db.scalars(select(memory_service.MemoryEmbedding).where(memory_service.MemoryEmbedding.memory_id == m.id))):
            db.delete(e)
        if memory_service.embedding_model(db, m.org_id) is not None:
            enqueue(db, "memory.embed", org_id=m.org_id, user_id=user.id, payload={"memory_id": str(m.id)})
    if body.tags is not None:
        m.tags = body.tags
    return memory_service.memory_dict(m)


@router.delete("/{memory_id}", status_code=204)
def delete_memory(memory_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> None:
    """Hard delete: the entry and its embeddings are removed immediately (ON DELETE CASCADE)."""
    m = _get(db, user, memory_id, write=True)
    org_id, scope = m.org_id, m.scope
    db.delete(m)
    audit(db, "memory.delete", user_id=user.id, org_id=org_id, target_type="memory", target_id=memory_id, ip=client_ip(request), scope=scope)
