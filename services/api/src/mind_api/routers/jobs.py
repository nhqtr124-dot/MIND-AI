from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..db import session_scope, utcnow
from ..deps import DB, CurrentUser, require_org, resolve_scope
from ..models import Job
from ..schemas import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])
TERMINAL = {"completed", "failed", "cancelled", "partially_completed"}


def get_job(db: DB, user: CurrentUser, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    _, role = require_org(db, user, job.org_id)
    if job.user_id != user.id and role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _public(job: Job) -> JobOut:
    out = JobOut.model_validate(job)
    if out.error and "traceback" in out.error:
        out.error = {k: v for k, v in out.error.items() if k != "traceback"}
    return out


@router.get("", response_model=list[JobOut])
def list_jobs(user: CurrentUser, db: DB, org_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None, kind: str | None = None, limit: int = 50) -> list[JobOut]:
    scope = resolve_scope(db, user, org_id, project_id)
    q = select(Job).where(Job.org_id == scope.org_id)
    if scope.role not in ("owner", "admin"):
        q = q.where(Job.user_id == user.id)
    if scope.project_id:
        q = q.where(Job.project_id == scope.project_id)
    if kind:
        q = q.where(Job.kind == kind)
    return [_public(j) for j in db.scalars(q.order_by(Job.created_at.desc()).limit(min(limit, 200)))]


@router.get("/{job_id}", response_model=JobOut)
def read_job(job_id: uuid.UUID, user: CurrentUser, db: DB) -> JobOut:
    return _public(get_job(db, user, job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, user: CurrentUser, db: DB) -> JobOut:
    job = get_job(db, user, job_id)
    if job.status in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job already {job.status}")
    job.cancel_requested = True
    if job.status == "planned":
        job.status, job.finished_at, job.message = "cancelled", utcnow(), "cancelled before start"
    return _public(job)


@router.get("/{job_id}/events")
async def job_events(job_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> StreamingResponse:
    """Server-sent events with job progress until the job reaches a terminal state."""
    get_job(db, user, job_id)

    async def gen() -> AsyncIterator[bytes]:
        last = None
        for _ in range(3600):
            if await request.is_disconnected():
                return

            def snap() -> dict[str, object] | None:
                with session_scope() as s:
                    j = s.get(Job, job_id)
                    return _public(j).model_dump(mode="json") if j else None

            data = await asyncio.to_thread(snap)
            if data is None:
                return
            key = (data["status"], data["progress"], data["message"])
            if key != last:
                last = key
                yield f"event: job\ndata: {json.dumps(data)}\n\n".encode()
            if data["status"] in TERMINAL:
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
