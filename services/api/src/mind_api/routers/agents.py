from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from ..db import utcnow
from ..deps import DB, CurrentUser, client_ip, require_org, resolve_scope
from ..jobs import enqueue
from ..models import AgentRun, AgentTask, Approval, ScheduledJob, ToolExecution, Workflow
from ..schemas import ApprovalDecision, ApprovalOut, RunCreate, RunCreated, WorkflowCreate, WorkflowOut
from ..services.agents import PlanError, materialize_plan, run_dict, start_run, validate_plan
from ..services.audit import audit
from ..services.scheduler import next_run, trigger_workflow
from ..services.tools import tool_catalog

router = APIRouter(tags=["agents"])


@router.get("/agents/tools")
def tools() -> list[dict[str, Any]]:
    return tool_catalog()


def get_run(db: DB, user: CurrentUser, run_id: uuid.UUID) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    _, role = require_org(db, user, run.org_id)
    if run.user_id != user.id and role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.post("/agents/runs", response_model=RunCreated, status_code=202)
def create_run(body: RunCreate, request: Request, user: CurrentUser, db: DB) -> RunCreated:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    run = AgentRun(
        org_id=scope.org_id, project_id=scope.project_id, user_id=user.id, goal=body.goal,
        plan_source="user" if body.plan else "llm", budget_usd=body.budget_usd, time_limit_s=body.time_limit_s,
    )  # fmt: skip
    db.add(run)
    db.flush()
    if body.plan:
        try:
            materialize_plan(db, run, validate_plan(body.plan))
        except PlanError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    job_id = start_run(db, run)
    audit(
        db,
        "agent.run_created",
        user_id=user.id,
        org_id=scope.org_id,
        target_type="agent_run",
        target_id=run.id,
        ip=client_ip(request),
        plan_source=run.plan_source,
    )
    return RunCreated(job_id=job_id, run_id=run.id)


@router.get("/agents/runs")
def list_runs(
    user: CurrentUser,
    db: DB,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    scope = resolve_scope(db, user, org_id, project_id)
    q = select(AgentRun).where(AgentRun.org_id == scope.org_id)
    if scope.role not in ("owner", "admin"):
        q = q.where(AgentRun.user_id == user.id)
    if scope.project_id:
        q = q.where(AgentRun.project_id == scope.project_id)
    return [
        run_dict(r, with_tasks=False)
        for r in db.scalars(q.order_by(AgentRun.created_at.desc()).limit(min(limit, 200)))
    ]


@router.get("/agents/runs/{run_id}")
def read_run(run_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    return run_dict(get_run(db, user, run_id))


@router.get("/agents/runs/{run_id}/executions")
def run_executions(run_id: uuid.UUID, user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    run = get_run(db, user, run_id)
    rows = db.scalars(
        select(ToolExecution).where(ToolExecution.run_id == run.id).order_by(ToolExecution.created_at)
    )
    return [
        {
            "id": str(e.id),
            "task_id": str(e.task_id) if e.task_id else None,
            "tool": e.tool,
            "args": e.args,
            "status": e.status,
            "error": e.error,
            "duration_ms": e.duration_ms,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@router.post("/agents/runs/{run_id}/cancel")
def cancel_run(run_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    run = get_run(db, user, run_id)
    if run.status in ("completed", "failed", "cancelled", "partially_completed"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Run already {run.status}")
    run.cancel_requested = True
    if run.status == "awaiting_approval":
        # No job is running; finish the cancellation now.
        for t in run.tasks:
            if t.status in ("planned", "awaiting_approval"):
                t.status, t.error, t.finished_at = "cancelled", "cancelled by user", utcnow()
        for a in db.scalars(select(Approval).where(Approval.run_id == run.id, Approval.status == "pending")):
            a.status, a.decided_at, a.decision_note = "expired", utcnow(), "run cancelled"
        run.status, run.finished_at = "cancelled", utcnow()
    audit(
        db,
        "agent.run_cancelled",
        user_id=user.id,
        org_id=run.org_id,
        target_type="agent_run",
        target_id=run.id,
    )
    return run_dict(run)


# ---------------------------------------------------------------------------- approvals


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(
    org_id: uuid.UUID, user: CurrentUser, db: DB, status_filter: str = "pending"
) -> list[Approval]:
    _, role = require_org(db, user, org_id)
    q = select(Approval).where(Approval.org_id == org_id)
    if status_filter != "all":
        q = q.where(Approval.status == status_filter)
    if role not in ("owner", "admin"):
        q = q.where(Approval.requested_by == user.id)
    return list(db.scalars(q.order_by(Approval.created_at.desc()).limit(200)))


@router.post("/approvals/{approval_id}", response_model=ApprovalOut)
def decide(
    approval_id: uuid.UUID, body: ApprovalDecision, request: Request, user: CurrentUser, db: DB
) -> Approval:
    a = db.get(Approval, approval_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    _, role = require_org(db, user, a.org_id)
    is_admin = role in ("owner", "admin")
    if a.risk in ("high", "critical") and not is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{a.risk}-risk actions must be approved by an organization admin or owner",
        )
    if not is_admin and a.requested_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    if a.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Approval already {a.status}")
    a.status = "approved" if body.decision == "approve" else "rejected"
    a.decided_by, a.decided_at, a.decision_note = user.id, utcnow(), body.note
    audit(
        db,
        f"approval.{a.status}",
        user_id=user.id,
        org_id=a.org_id,
        target_type="approval",
        target_id=a.id,
        ip=client_ip(request),
        tool=a.action,
        risk=a.risk,
    )
    if a.run_id:
        run = db.get(AgentRun, a.run_id)
        if run and run.status == "awaiting_approval":
            if a.task_id:
                t = db.get(AgentTask, a.task_id)
                if t and t.status == "awaiting_approval":
                    t.status = "planned"
            run.status = "running"
            enqueue(
                db,
                "agent.run",
                org_id=run.org_id,
                user_id=run.user_id,
                project_id=run.project_id,
                payload={"run_id": str(run.id)},
                max_attempts=1,
            )
    return a


# ---------------------------------------------------------------------------- workflows


def _wf_out(db: DB, w: Workflow) -> WorkflowOut:
    sj = db.scalar(select(ScheduledJob).where(ScheduledJob.workflow_id == w.id))
    return WorkflowOut(
        id=w.id, org_id=w.org_id, project_id=w.project_id, name=w.name, definition=w.definition, enabled=w.enabled,
        cron=sj.cron if sj else None, timezone=sj.timezone if sj else None, next_run_at=sj.next_run_at if sj and sj.enabled else None,
        last_run_at=sj.last_run_at if sj else None, created_at=w.created_at,
    )  # fmt: skip


@router.get("/workflows", response_model=list[WorkflowOut])
def list_workflows(
    user: CurrentUser, db: DB, org_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None
) -> list[WorkflowOut]:
    scope = resolve_scope(db, user, org_id, project_id)
    q = select(Workflow).where(Workflow.org_id == scope.org_id)
    if scope.project_id:
        q = q.where(Workflow.project_id == scope.project_id)
    return [_wf_out(db, w) for w in db.scalars(q.order_by(Workflow.created_at.desc()))]


@router.post("/workflows", response_model=WorkflowOut, status_code=201)
def create_workflow(body: WorkflowCreate, user: CurrentUser, db: DB) -> WorkflowOut:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    try:
        validate_plan(body.definition)
    except PlanError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    w = Workflow(
        org_id=scope.org_id,
        project_id=scope.project_id,
        name=body.name,
        definition=body.definition,
        created_by=user.id,
    )
    db.add(w)
    db.flush()
    if body.cron:
        if not croniter.is_valid(body.cron):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cron expression")
        try:
            ZoneInfo(body.timezone)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown time zone") from exc
        db.add(
            ScheduledJob(
                workflow_id=w.id,
                cron=body.cron,
                timezone=body.timezone,
                next_run_at=next_run(body.cron, body.timezone, datetime.now(UTC)),
            )
        )
        db.flush()
    audit(
        db,
        "workflow.create",
        user_id=user.id,
        org_id=scope.org_id,
        target_type="workflow",
        target_id=w.id,
        cron=body.cron,
    )
    return _wf_out(db, w)


def _get_wf(db: DB, user: CurrentUser, wf_id: uuid.UUID, min_role: str = "viewer") -> Workflow:
    w = db.get(Workflow, wf_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    resolve_scope(db, user, w.org_id, w.project_id, min_role)
    return w


@router.post("/workflows/{wf_id}/run")
def run_workflow_now(wf_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    w = _get_wf(db, user, wf_id, "editor")
    run = trigger_workflow(db, w, user.id, "manual")
    return {"run_id": str(run.id)}


@router.patch("/workflows/{wf_id}", response_model=WorkflowOut)
def toggle_workflow(wf_id: uuid.UUID, enabled: bool, user: CurrentUser, db: DB) -> WorkflowOut:
    w = _get_wf(db, user, wf_id, "editor")
    w.enabled = enabled
    sj = db.scalar(select(ScheduledJob).where(ScheduledJob.workflow_id == w.id))
    if sj:
        sj.enabled = enabled
        if enabled:
            sj.next_run_at = next_run(sj.cron, sj.timezone, datetime.now(UTC))
    return _wf_out(db, w)


@router.delete("/workflows/{wf_id}", status_code=204)
def delete_workflow(wf_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    w = _get_wf(db, user, wf_id, "editor")
    db.delete(w)
    audit(db, "workflow.delete", user_id=user.id, org_id=w.org_id, target_type="workflow", target_id=wf_id)
