"""Tools available to agents.

Every tool declares a risk level. ``low`` tools run automatically inside the
user's own permission scope; anything higher pauses the run for a human
approval. Each tool also has a verifier, run as the Verification agent, that
checks evidence (stored files, validation reports, exit codes) rather than
trusting the tool's own success flag.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mind_cad import TEMPLATES, CadError, generate_part
from mind_research import Fetcher, ResearchError
from mind_sandbox import Limits, SandboxError, docker_available, materialize, run
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import GeneratedArtifact, MemoryEntry, ProjectFile
from ..storage import get_storage
from . import llm
from .artifacts import create_artifact, store_version
from .docs_service import build_document

Risk = Literal["low", "medium", "high", "critical"]


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    org_id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID | None
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None


@dataclass
class Tool:
    name: str
    agent: str
    description: str
    Args: type[BaseModel]
    risk: Risk
    run: Callable[[ToolContext, Any], dict[str, Any]]
    verify: Callable[[dict[str, Any]], dict[str, Any]]
    available: Callable[[], tuple[bool, str]] = lambda: (True, "")


def _check(ok: bool, detail: str) -> dict[str, Any]:
    return {"passed": ok, "detail": detail}


def _artifact_verifier(require_validation_pass: bool = True) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def verify(out: dict[str, Any]) -> dict[str, Any]:
        aid = out.get("artifact_id")
        if not aid:
            return _check(False, "tool reported no artifact")
        with session_scope() as db:
            a = db.get(GeneratedArtifact, uuid.UUID(aid))
            if a is None or not a.versions:
                return _check(False, "artifact record or version missing")
            storage = get_storage()
            missing = [f["name"] for f in a.versions[-1].files if not storage.exists(f["storage_key"])]
            if missing:
                return _check(False, f"files missing from storage: {missing}")
            if require_validation_pass and a.validation_status in ("validation_failed", "failed", "generation_failed"):
                return _check(False, f"artifact validation status is '{a.validation_status}'")
            return _check(True, f"{len(a.versions[-1].files)} stored file(s); validation status '{a.validation_status}'")

    return verify


# ---------------------------------------------------------------------------- CAD


class CadArgs(BaseModel):
    template: str = Field(description=f"one of: {', '.join(TEMPLATES)}")
    params: dict[str, Any] = Field(default_factory=dict)
    formats: list[Literal["stl", "step", "3mf"]] = ["stl", "step"]
    title: str = "CAD part"


def _cad(ctx: ToolContext, a: CadArgs) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mind-agent-cad-") as tmp:
        try:
            res = generate_part(a.template, a.params, Path(tmp), formats=tuple(a.formats))
        except CadError as exc:
            raise ToolError(f"CAD generation failed: {exc}") from exc
        report = res.to_dict()
        for f in report["files"]:
            f.pop("path", None)
        with session_scope() as db:
            art = create_artifact(db, org_id=ctx.org_id, project_id=ctx.project_id, user_id=ctx.user_id, kind="cad", title=a.title,
                                  source={"tool": "cad.generate_part", "run_id": str(ctx.run_id), "template": a.template})  # fmt: skip
            paths = [(Path(f.path), {"format": f.format, "part": f.part}) for f in res.files] + [(Path(tmp) / "report.json", {"format": "json", "part": "report"})]
            store_version(db, art, paths, validation=report, params=res.params, user_id=ctx.user_id)
            art.validation_status = res.status
            art.status = "completed" if res.status != "validation_failed" else "partially_completed"
            aid = art.id
    return {"artifact_id": str(aid), "validation_status": res.status, "bom": res.bom, "assumptions": res.assumptions}


# ---------------------------------------------------------------------------- documents


class DocArgs(BaseModel):
    kind: Literal["document", "presentation", "spreadsheet"] = "document"
    format: Literal["docx", "pdf", "md", "html", "txt", "pptx", "xlsx", "csv"] = "docx"
    spec: dict[str, Any]
    title: str = "Document"


def _doc(ctx: ToolContext, a: DocArgs) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mind-agent-doc-") as tmp:
        try:
            path, report, previews = build_document(a.kind, a.format, a.spec, Path(tmp))
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"document generation failed: {exc}") from exc
        with session_scope() as db:
            kind = {"document": "document", "presentation": "presentation", "spreadsheet": "spreadsheet"}[a.kind]
            art = create_artifact(db, org_id=ctx.org_id, project_id=ctx.project_id, user_id=ctx.user_id, kind=kind, title=a.title,
                                  source={"tool": "documents.generate", "run_id": str(ctx.run_id)})  # fmt: skip
            store_version(db, art, [(path, {"format": a.format, "role": "document"})] + [(p, {"format": "png", "role": "preview"}) for p in previews],
                          validation=report, params={"kind": a.kind, "format": a.format}, user_id=ctx.user_id)  # fmt: skip
            art.validation_status = "passed" if report["passed"] else "failed"
            art.status = "completed" if report["passed"] else "failed"
            aid = art.id
    if not report["passed"]:
        raise ToolError(f"generated document failed validation: {[c for c in report['checks'] if c['status'] == 'fail']}")
    return {"artifact_id": str(aid), "validation": report}


# ---------------------------------------------------------------------------- code


class CodeArgs(BaseModel):
    runtime: Literal["python", "node"] = "python"
    command: list[str] = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict, description="inline files; merged over the project workspace")
    use_project_workspace: bool = True
    timeout_s: float = Field(30, le=120)


def _workspace_files(project_id: uuid.UUID | None) -> dict[str, bytes]:
    if project_id is None:
        return {}
    storage = get_storage()
    with session_scope() as db:
        rows = db.scalars(select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.kind == "workspace")).all()
        return {f.path: storage.get_bytes(f.storage_key) for f in rows}


def _code(ctx: ToolContext, a: CodeArgs) -> dict[str, Any]:
    if not (get_settings().sandbox_enabled and docker_available()):
        raise ToolError("sandboxed execution is unavailable (Docker not reachable or sandbox disabled)")
    files: dict[str, bytes | str] = dict(_workspace_files(ctx.project_id) if a.use_project_workspace else {})
    files.update(a.files)
    try:
        wd = materialize(files)
        r = run(wd, a.command, runtime=a.runtime, limits=Limits(timeout_s=a.timeout_s))
    except SandboxError as exc:
        raise ToolError(str(exc)) from exc
    return {"exit_code": r.exit_code, "stdout": r.stdout[-8000:], "stderr": r.stderr[-8000:], "timed_out": r.timed_out, "duration_s": r.duration_s, "image": r.image}


def _verify_code(out: dict[str, Any]) -> dict[str, Any]:
    return _check(out.get("exit_code") == 0 and not out.get("timed_out"), f"exit code {out.get('exit_code')}, timed out: {out.get('timed_out')}")


# ---------------------------------------------------------------------------- research


class FetchArgs(BaseModel):
    url: str


def _fetch(ctx: ToolContext, a: FetchArgs) -> dict[str, Any]:
    async def go() -> Any:
        f = Fetcher()
        try:
            return await f.fetch(a.url)
        finally:
            await f.client.aclose()

    try:
        page = asyncio.run(go())
    except ResearchError as exc:
        raise ToolError(exc.message) from exc
    return {"url": page.final_url, "title": page.title, "retrieved_at": page.retrieved_at, "sha256": page.sha256, "text": page.text[:20000], "chars": len(page.text)}


def _verify_fetch(out: dict[str, Any]) -> dict[str, Any]:
    return _check(bool(out.get("text")) and len(out.get("sha256", "")) == 64, f"{out.get('chars', 0)} characters retrieved from {out.get('url')}")


# ---------------------------------------------------------------------------- text generation


class GenerateArgs(BaseModel):
    prompt: str = Field(min_length=1)
    max_output_tokens: int = Field(2048, le=16000)


def _generate(ctx: ToolContext, a: GenerateArgs) -> dict[str, Any]:
    try:
        r = llm.complete_sync(ctx.org_id, ctx.user_id, "You are a helpful expert. Answer precisely.", a.prompt, max_output_tokens=a.max_output_tokens, purpose="agent")
    except llm.LLMUnavailable as exc:
        raise ToolError(str(exc)) from exc
    return {"text": r.text, "model": r.model, "provider": r.provider, "cost_usd": str(r.cost_usd) if r.cost_usd is not None else None}


# ---------------------------------------------------------------------------- memory / files (approval required)


class MemoryArgs(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    scope: Literal["user", "team", "project"] = "user"


def _memory_save(ctx: ToolContext, a: MemoryArgs) -> dict[str, Any]:
    if a.scope == "project" and ctx.project_id is None:
        raise ToolError("project-scoped memory requires a project")
    with session_scope() as db:
        m = MemoryEntry(org_id=ctx.org_id, user_id=ctx.user_id, scope=a.scope, project_id=ctx.project_id if a.scope == "project" else None, content=a.content, source="agent")
        db.add(m)
        db.flush()
        return {"memory_id": str(m.id), "scope": a.scope}


def _verify_memory(out: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as db:
        return _check(db.get(MemoryEntry, uuid.UUID(out["memory_id"])) is not None, "memory entry stored")


class DeleteFileArgs(BaseModel):
    path: str


def _delete_file(ctx: ToolContext, a: DeleteFileArgs) -> dict[str, Any]:
    if ctx.project_id is None:
        raise ToolError("no project in scope")
    with session_scope() as db:
        f = db.scalar(select(ProjectFile).where(ProjectFile.project_id == ctx.project_id, ProjectFile.kind == "workspace", ProjectFile.path == a.path))
        if f is None:
            raise ToolError(f"file not found: {a.path}")
        get_storage().delete(f.storage_key)
        db.delete(f)
    return {"deleted": a.path}


def _verify_deleted(out: dict[str, Any]) -> dict[str, Any]:
    return _check("deleted" in out, f"deleted {out.get('deleted')}")


def _sandbox_available() -> tuple[bool, str]:
    ok = get_settings().sandbox_enabled and docker_available()
    return ok, "" if ok else "Docker sandbox unavailable"


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool("cad.generate_part", "cad", "Generate a parametric 3D part (STL/STEP) from a template and validate it.", CadArgs, "low", _cad, _artifact_verifier()),  # type: ignore[arg-type]
        Tool("documents.generate", "document", "Generate a DOCX/PDF/MD/HTML/TXT document, PPTX deck or XLSX/CSV sheet from a structured spec.", DocArgs, "low", _doc, _artifact_verifier()),  # type: ignore[arg-type]
        Tool("code.run", "testing", "Run a command in an isolated, offline sandbox over the project workspace.", CodeArgs, "low", _code, _verify_code, _sandbox_available),  # type: ignore[arg-type]
        Tool("research.fetch_url", "research", "Fetch a public web page (robots.txt honoured) and return its text with provenance.", FetchArgs, "low", _fetch, _verify_fetch),  # type: ignore[arg-type]
        Tool("llm.generate", "orchestrator", "Ask a language model to write or analyse text.", GenerateArgs, "low", _generate, lambda o: _check(bool(o.get("text")), f"{len(o.get('text', ''))} characters from {o.get('model')}")),  # type: ignore[arg-type]
        Tool("memory.save", "orchestrator", "Persist a fact to long-term memory (requires approval).", MemoryArgs, "medium", _memory_save, _verify_memory),  # type: ignore[arg-type]
        Tool("files.delete", "coding", "Delete a file from the project workspace (destructive; requires approval).", DeleteFileArgs, "high", _delete_file, _verify_deleted),  # type: ignore[arg-type]
    )
}

AGENT_TITLES = {
    "orchestrator": "MIND Orchestrator",
    "research": "Research Agent",
    "coding": "Coding Agent",
    "testing": "Testing Agent",
    "cad": "CAD Agent",
    "document": "Document Agent",
    "verification": "Verification Agent",
    "security": "Security Agent",
}


def tool_catalog() -> list[dict[str, Any]]:
    out = []
    for t in TOOLS.values():
        ok, why = t.available()
        out.append(
            {
                "name": t.name,
                "agent": t.agent,
                "agent_title": AGENT_TITLES.get(t.agent, t.agent),
                "description": t.description,
                "risk": t.risk,
                "requires_approval": t.risk != "low",
                "available": ok,
                "unavailable_reason": why or None,
                "args_schema": t.Args.model_json_schema(),
            }
        )
    return out
