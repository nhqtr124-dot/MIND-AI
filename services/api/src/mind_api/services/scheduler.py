"""Scheduled workflows: each due schedule starts an agent run from the workflow's plan."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import AgentRun, ScheduledJob, Workflow
from .agents import materialize_plan, start_run, validate_plan

log = logging.getLogger("mind.scheduler")


def next_run(cron: str, tz: str, after: datetime) -> datetime:
    local = after.astimezone(ZoneInfo(tz))
    return croniter(cron, local).get_next(datetime).astimezone(ZoneInfo("UTC"))


def trigger_workflow(db: Session, wf: Workflow, user_id, reason: str) -> AgentRun:  # type: ignore[no-untyped-def]
    plan = validate_plan(wf.definition)
    run = AgentRun(
        org_id=wf.org_id,
        project_id=wf.project_id,
        user_id=user_id,
        goal=f"Workflow '{wf.name}' ({reason})",
        plan_source="user",
        time_limit_s=int(wf.definition.get("time_limit_s", 900)),
    )
    db.add(run)
    db.flush()
    materialize_plan(db, run, plan)
    start_run(db, run)
    return run


def enqueue_due_workflows(db: Session) -> int:
    now = utcnow()
    n = 0
    due = db.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)
        .with_for_update(skip_locked=True)
    ).all()
    for sj in due:
        wf = db.get(Workflow, sj.workflow_id)
        sj.next_run_at = next_run(sj.cron, sj.timezone, now)
        if wf is None or not wf.enabled or wf.created_by is None:
            continue
        try:
            run = trigger_workflow(db, wf, wf.created_by, "scheduled")
        except Exception:  # noqa: BLE001 - a broken workflow must not stop the scheduler
            log.exception("scheduled workflow %s failed to start", wf.id)
            continue
        sj.last_run_at, sj.last_job_id = now, run.id
        n += 1
    return n
