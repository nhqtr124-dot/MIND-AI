"""Durable job queue on PostgreSQL.

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and held under a lease. A
worker that dies leaves an expired lease; the next claim pass returns the job to
the queue (or fails it once attempts are exhausted), so jobs are resumable.
"""

from __future__ import annotations

import logging
import os
import socket
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from .config import get_settings
from .db import session_scope, utcnow
from .models import Job

log = logging.getLogger("mind.jobs")


class JobCancelled(Exception):
    pass


class RetryableJobError(Exception):
    """Raise from a handler for transient failures; the job is retried with backoff."""


class JobFailed(Exception):
    """Raise from a handler for a clean, user-facing failure (no traceback stored)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass
class JobContext:
    job_id: uuid.UUID
    kind: str
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    project_id: uuid.UUID | None
    payload: dict[str, Any]
    attempt: int

    def progress(self, pct: int, message: str = "") -> None:
        lease = timedelta(seconds=get_settings().job_lease_seconds)
        with session_scope() as db:
            db.execute(
                update(Job)
                .where(Job.id == self.job_id)
                .values(progress=max(0, min(100, pct)), message=message[:2000], lease_expires_at=utcnow() + lease)
            )
            cancelled = db.scalar(select(Job.cancel_requested).where(Job.id == self.job_id))
        if cancelled:
            raise JobCancelled()

    def cancelled(self) -> bool:
        with session_scope() as db:
            return bool(db.scalar(select(Job.cancel_requested).where(Job.id == self.job_id)))


Handler = Callable[[JobContext], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(
    db: Session,
    kind: str,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> Job:
    job = Job(kind=kind, org_id=org_id, user_id=user_id, project_id=project_id, payload=payload or {}, max_attempts=max_attempts, message="queued")
    db.add(job)
    db.flush()
    return job


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def reclaim_expired(db: Session) -> int:
    """Return jobs whose worker lease expired to the queue, or fail them if out of attempts."""
    res = db.execute(
        text(
            """
            UPDATE jobs SET
              status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'planned' END,
              error = CASE WHEN attempts >= max_attempts
                      THEN jsonb_build_object('message', 'worker lease expired; attempts exhausted') ELSE error END,
              finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
              locked_by = NULL, lease_expires_at = NULL,
              message = 'requeued after worker lease expired'
            WHERE status = 'running' AND lease_expires_at < now()
            """
        )
    )
    return res.rowcount or 0  # type: ignore[attr-defined]


def claim(db: Session, wid: str, kinds: list[str] | None = None) -> Job | None:
    lease = get_settings().job_lease_seconds
    kinds = kinds or list(HANDLERS)
    row = db.execute(
        text(
            """
            UPDATE jobs SET status = 'running', locked_by = :wid, attempts = attempts + 1,
                   started_at = COALESCE(started_at, now()), lease_expires_at = now() + make_interval(secs => :lease),
                   message = 'running'
            WHERE id = (
              SELECT id FROM jobs
              WHERE status = 'planned' AND run_after <= now() AND NOT cancel_requested AND kind = ANY(:kinds)
              ORDER BY created_at
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            RETURNING id
            """
        ),
        {"wid": wid, "lease": lease, "kinds": kinds},
    ).first()
    if row is None:
        return None
    return db.get(Job, row[0], populate_existing=True)


def run_job(job_id: uuid.UUID) -> str:
    """Execute a claimed job and persist the outcome. Returns the final status."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job is not None
        ctx = JobContext(job.id, job.kind, job.org_id, job.user_id, job.project_id, dict(job.payload), job.attempts)
        fn = HANDLERS.get(job.kind)
    status, result, error, retry_delay = "failed", None, None, None
    if fn is None:
        error = {"message": f"no handler registered for job kind '{ctx.kind}'"}
    else:
        try:
            result = fn(ctx)
            status = "completed"
            if isinstance(result, dict) and result.get("_status") in ("partially_completed", "failed"):
                status = result.pop("_status")
        except JobCancelled:
            status, error = "cancelled", {"message": "cancelled by user"}
        except JobFailed as exc:
            error = {"message": str(exc), **exc.details}
        except RetryableJobError as exc:
            error = {"message": str(exc), "retryable": True}
            retry_delay = min(300, 5 * 2 ** (ctx.attempt - 1))
        except Exception as exc:  # noqa: BLE001 - store and report every unexpected failure
            log.exception("job %s (%s) crashed", ctx.job_id, ctx.kind)
            error = {"message": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]}
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job is not None
        if retry_delay is not None and job.attempts < job.max_attempts and not job.cancel_requested:
            job.status, job.error, job.locked_by, job.lease_expires_at = "planned", error, None, None
            job.run_after = utcnow() + timedelta(seconds=retry_delay)
            job.message = f"retrying in {retry_delay}s: {error['message'] if error else ''}"[:2000]
            return "planned"
        job.status = status
        job.result = result
        job.error = error
        job.progress = 100 if status == "completed" else job.progress
        job.message = {"completed": "completed", "cancelled": "cancelled", "partially_completed": "partially completed"}.get(
            status, (error or {}).get("message", "failed")
        )[:2000]
        job.finished_at = utcnow()
        job.locked_by = None
        job.lease_expires_at = None
    return status


def run_pending(max_jobs: int = 50, kinds: list[str] | None = None) -> int:
    """Process queued jobs synchronously (used by tests and the CLI)."""
    n = 0
    wid = worker_id()
    while n < max_jobs:
        with session_scope() as db:
            reclaim_expired(db)
            job = claim(db, wid, kinds)
            jid = job.id if job else None
        if jid is None:
            break
        run_job(jid)
        n += 1
    return n
