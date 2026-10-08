"""Agent orchestration engine.

USER REQUEST -> PLAN (LLM or user-supplied) -> validation -> DAG execution
(independent tasks in parallel) -> per-task verification -> run status.

Runs pause (status ``awaiting_approval``) when a task needs a human decision;
deciding the approval re-queues the run, which resumes where it stopped.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import session_scope, utcnow
from ..jobs import JobContext, enqueue, handler
from ..models import AgentRun, AgentTask, Approval, ToolExecution, UsageEvent
from . import llm
from .audit import audit, redact
from .tools import AGENT_TITLES, TOOLS, ToolContext, ToolError, tool_catalog

MAX_TASKS = 25
_REF = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\.]+)\s*\}\}")


class PlanTask(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,60}$")
    tool: str
    title: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    tasks: list[PlanTask] = Field(min_length=1, max_length=MAX_TASKS)


class PlanError(ValueError):
    pass


def validate_plan(raw: dict[str, Any]) -> Plan:
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        raise PlanError(f"invalid plan structure: {exc.errors()[:3]}") from exc
    keys = [t.key for t in plan.tasks]
    if len(set(keys)) != len(keys):
        raise PlanError("task keys must be unique")
    for t in plan.tasks:
        if t.tool not in TOOLS:
            raise PlanError(f"task '{t.key}': unknown tool '{t.tool}'")
        for d in t.depends_on:
            if d not in keys or d == t.key:
                raise PlanError(f"task '{t.key}': invalid dependency '{d}'")
        # Args with {{ref}} placeholders are validated after substitution at run time.
        if not _REF.search(json.dumps(t.args)):
            try:
                TOOLS[t.tool].Args.model_validate(t.args)
            except ValidationError as exc:
                raise PlanError(f"task '{t.key}': invalid args for {t.tool}: {exc.errors()[:2]}") from exc
    # cycle check (Kahn)
    deps = {t.key: set(t.depends_on) for t in plan.tasks}
    done: set[str] = set()
    while deps:
        ready = [k for k, d in deps.items() if d <= done]
        if not ready:
            raise PlanError(f"dependency cycle among tasks: {sorted(deps)}")
        for k in ready:
            done.add(k)
            deps.pop(k)
    return plan


def materialize_plan(db: Session, run: AgentRun, plan: Plan) -> None:
    for i, t in enumerate(plan.tasks):
        db.add(
            AgentTask(
                run_id=run.id, position=i, key=t.key, agent=TOOLS[t.tool].agent, tool=t.tool,
                title=t.title or t.tool, args=t.args, depends_on=t.depends_on,
            )
        )  # fmt: skip
    run.plan = plan.model_dump()


PLANNER_SYSTEM = """You are the MIND Orchestrator. Turn the user's goal into an execution plan that uses ONLY the tools listed.
Return ONLY a JSON object: {"tasks": [{"key": "short_id", "tool": "<tool name>", "title": "what this step does", "args": {...}, "depends_on": ["key", ...]}]}
Rules:
- Use the minimum number of steps. Tasks without dependencies run in parallel.
- args must match the tool's JSON schema exactly.
- To use an earlier task's output in args, write the string "{{task_key.field}}" (e.g. "{{fetch.text}}").
- If the goal cannot be achieved with these tools, return {"tasks": [{"key": "explain", "tool": "llm.generate", "title": "Explain limitation", "args": {"prompt": "<explain which capability is missing>"}}]}.
"""


def plan_with_llm(org_id: uuid.UUID, user_id: uuid.UUID, goal: str) -> Plan:
    tools = [{k: v for k, v in t.items() if k in ("name", "description", "risk", "args_schema")} for t in tool_catalog() if t["available"]]
    prompt = f"Available tools (JSON):\n{json.dumps(tools)}\n\nGoal:\n{goal}"
    last_err = ""
    for _ in range(2):
        r = llm.complete_sync(org_id, user_id, PLANNER_SYSTEM, prompt + (f"\n\nYour previous plan was invalid: {last_err}. Fix it." if last_err else ""), max_output_tokens=4000, purpose="agent")
        try:
            return validate_plan(llm.extract_json(r.text))
        except (PlanError, ValueError) as exc:
            last_err = str(exc)[:500]
    raise PlanError(f"the model did not produce a valid plan: {last_err}")


def _resolve_refs(value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, str):
        whole = _REF.fullmatch(value.strip())
        if whole:
            return _lookup(outputs, whole.group(1), whole.group(2))
        return _REF.sub(lambda m: str(_lookup(outputs, m.group(1), m.group(2))), value)
    if isinstance(value, dict):
        return {k: _resolve_refs(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, outputs) for v in value]
    return value


def _lookup(outputs: dict[str, dict[str, Any]], key: str, path: str) -> Any:
    cur: Any = outputs.get(key)
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ToolError(f"reference {{{{{key}.{path}}}}} could not be resolved")
        cur = cur[part]
    return cur


def _execute_task(run_id: uuid.UUID, task_id: uuid.UUID) -> None:
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        run = db.get(AgentRun, run_id)
        assert task is not None and run is not None
        outputs = {t.key: t.output or {} for t in run.tasks if t.status == "completed"}
        task.status, task.started_at, task.attempts = "running", utcnow(), task.attempts + 1
        ctx = ToolContext(run.org_id, run.user_id, run.project_id, run.id, task.id)
        tool_name, raw_args = task.tool, dict(task.args)
    tool = TOOLS[tool_name]
    start = time.monotonic()
    output: dict[str, Any] | None = None
    error: str | None = None
    verification: dict[str, Any] | None = None
    try:
        args = tool.Args.model_validate(_resolve_refs(raw_args, outputs))
        output = tool.run(ctx, args)
        verification = {"agent": AGENT_TITLES["verification"], **tool.verify(output)}
        if not verification["passed"]:
            error = f"verification failed: {verification['detail']}"
    except (ToolError, ValidationError) as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded and surfaced; never swallowed silently
        error = f"{type(exc).__name__}: {exc}"
    ms = int((time.monotonic() - start) * 1000)
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        assert task is not None
        db.add(
            ToolExecution(
                org_id=ctx.org_id, user_id=ctx.user_id, run_id=run_id, task_id=task_id, tool=tool_name, args=redact(raw_args),
                status="succeeded" if error is None else "failed", output=output, error=error, duration_ms=ms,
            )
        )  # fmt: skip
        task.output, task.verification, task.error = output, verification, error
        if error is None:
            task.status, task.finished_at = "completed", utcnow()
        elif task.attempts < task.max_attempts and verification is None:
            task.status = "planned"  # retry tool errors; a verification failure is not retried blindly
        else:
            task.status, task.finished_at = "failed", utcnow()


def _spent(db: Session, run: AgentRun) -> Decimal:
    """Model spend attributed to this run's user since the run started (agent planning and llm.generate)."""
    started = run.started_at or run.created_at
    total = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
            UsageEvent.org_id == run.org_id, UsageEvent.user_id == run.user_id, UsageEvent.category == "agent", UsageEvent.created_at >= started
        )
    )
    return Decimal(total or 0)


@handler("agent.run")
def agent_run(ctx: JobContext) -> dict[str, Any]:
    run_id = uuid.UUID(ctx.payload["run_id"])
    with session_scope() as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            return {"skipped": "run deleted"}
        if run.status in ("completed", "failed", "cancelled", "partially_completed"):
            return {"skipped": f"run already {run.status}"}
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        needs_plan = not run.tasks and run.plan_source == "llm"
        org_id, user_id, goal = run.org_id, run.user_id, run.goal

    if needs_plan:
        ctx.progress(5, "Orchestrator is planning")
        try:
            plan = plan_with_llm(org_id, user_id, goal)
        except (PlanError, llm.LLMUnavailable) as exc:
            with session_scope() as db:
                run = db.get(AgentRun, run_id)
                assert run is not None
                run.status, run.error, run.finished_at = "failed", f"planning failed: {exc}", utcnow()
            return {"_status": "failed", "run_id": str(run_id), "error": str(exc)}
        with session_scope() as db:
            run = db.get(AgentRun, run_id)
            assert run is not None
            materialize_plan(db, run, plan)

    deadline: float | None = None
    with ThreadPoolExecutor(max_workers=3) as pool:
        while True:
            with session_scope() as db:
                run = db.get(AgentRun, run_id)
                assert run is not None
                db.refresh(run)
                tasks = list(run.tasks)
                if deadline is None:
                    elapsed = (datetime.now(UTC) - (run.started_at or utcnow())).total_seconds()
                    deadline = time.monotonic() + max(0.0, run.time_limit_s - elapsed)
                by_key = {t.key: t for t in tasks}
                stop_reason = None
                if run.cancel_requested:
                    stop_reason = "cancelled by user"
                elif time.monotonic() > deadline:
                    stop_reason = f"time limit of {run.time_limit_s}s reached"
                elif run.budget_usd is not None and _spent(db, run) >= run.budget_usd:
                    stop_reason = f"budget of ${run.budget_usd} reached"
                if stop_reason:
                    for t in tasks:
                        if t.status in ("planned", "awaiting_approval"):
                            t.status, t.error, t.finished_at = "cancelled", stop_reason, utcnow()
                    run.error = stop_reason
                # cancel tasks whose dependencies cannot complete
                changed = True
                while changed:
                    changed = False
                    for t in tasks:
                        if t.status == "planned" and any(by_key[d].status in ("failed", "cancelled") for d in t.depends_on):
                            t.status, t.error, t.finished_at = "cancelled", "a dependency did not complete", utcnow()
                            changed = True
                ready = [t for t in tasks if t.status == "planned" and all(by_key[d].status == "completed" for d in t.depends_on)]
                to_run: list[uuid.UUID] = []
                for t in ready:
                    tool = TOOLS[t.tool]
                    if tool.risk == "low":
                        to_run.append(t.id)
                        continue
                    appr = db.get(Approval, t.approval_id) if t.approval_id else None
                    if appr is None:
                        appr = Approval(
                            org_id=run.org_id, requested_by=run.user_id, run_id=run.id, task_id=t.id, action=t.tool, risk=tool.risk,
                            summary=f"{AGENT_TITLES.get(t.agent, t.agent)} wants to run '{t.tool}': {t.title}", details={"args": redact(t.args)},
                        )  # fmt: skip
                        db.add(appr)
                        db.flush()
                        t.approval_id, t.status = appr.id, "awaiting_approval"
                        audit(db, "approval.requested", user_id=run.user_id, org_id=run.org_id, target_type="approval", target_id=appr.id, tool=t.tool, risk=tool.risk)
                    elif appr.status == "approved":
                        to_run.append(t.id)
                    elif appr.status in ("rejected", "expired"):
                        t.status, t.error, t.finished_at = "cancelled", f"approval {appr.status}", utcnow()
                    else:
                        t.status = "awaiting_approval"
                pending = [t for t in tasks if t.status in ("planned", "running")]
                waiting = [t for t in tasks if t.status == "awaiting_approval"]
                done_n = sum(t.status in ("completed", "failed", "cancelled") for t in tasks)
            if to_run:
                ctx.progress(10 + int(80 * done_n / max(len(tasks), 1)), f"running {len(to_run)} task(s)")
                list(pool.map(lambda tid: _execute_task(run_id, tid), to_run))
                continue
            if pending and not waiting:
                continue  # retries queued
            break

    with session_scope() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        db.refresh(run)
        statuses = [t.status for t in run.tasks]
        if "awaiting_approval" in statuses and not run.cancel_requested:
            run.status = "awaiting_approval"
            return {"run_id": str(run_id), "status": run.status, "waiting_for_approval": statuses.count("awaiting_approval")}
        completed = statuses.count("completed")
        if run.cancel_requested:
            run.status = "cancelled"
        elif completed == len(statuses):
            run.status = "completed"
        elif completed == 0:
            run.status = "failed"
        else:
            run.status = "partially_completed"
        run.finished_at = utcnow()
        run.result = {
            "tasks": {t.key: {"status": t.status, "error": t.error, "verification": t.verification} for t in run.tasks},
            "artifacts": [t.output["artifact_id"] for t in run.tasks if t.output and t.output.get("artifact_id")],
        }
        final = run.status
    return {"run_id": str(run_id), "status": final, "_status": "completed" if final == "completed" else "partially_completed" if final == "partially_completed" else "failed"}


def start_run(db: Session, run: AgentRun) -> uuid.UUID:
    job = enqueue(db, "agent.run", org_id=run.org_id, user_id=run.user_id, project_id=run.project_id, payload={"run_id": str(run.id)}, max_attempts=1)
    return job.id


def run_dict(run: AgentRun, with_tasks: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(run.id),
        "org_id": str(run.org_id),
        "project_id": str(run.project_id) if run.project_id else None,
        "goal": run.goal,
        "status": run.status,
        "plan_source": run.plan_source,
        "budget_usd": str(run.budget_usd) if run.budget_usd is not None else None,
        "time_limit_s": run.time_limit_s,
        "error": run.error,
        "result": run.result,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    if with_tasks:
        d["tasks"] = [
            {
                "id": str(t.id),
                "key": t.key,
                "agent": t.agent,
                "agent_title": AGENT_TITLES.get(t.agent, t.agent),
                "tool": t.tool,
                "title": t.title,
                "args": redact(t.args),
                "depends_on": t.depends_on,
                "status": t.status,
                "attempts": t.attempts,
                "output": t.output,
                "verification": t.verification,
                "error": t.error,
                "approval_id": str(t.approval_id) if t.approval_id else None,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            }
            for t in run.tasks
        ]
    return d
