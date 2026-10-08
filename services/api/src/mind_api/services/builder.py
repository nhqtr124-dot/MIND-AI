"""MIND Builder: project workspaces, AI edits with test-and-repair, snapshots, export."""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import uuid
import zipfile
from typing import Any

from mind_sandbox import PROJECT_TEMPLATES, Limits, SandboxError, docker_available, materialize, run
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..jobs import JobContext, JobFailed, handler
from ..models import Project, ProjectFile
from ..storage import get_storage
from . import llm
from .artifacts import create_artifact, guess_mime, store_version

MAX_FILE_BYTES = 1_000_000
MAX_FILES = 500
MAX_TOTAL_BYTES = 20_000_000
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_\-./ ]{1,300}$")
TEXT_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".svg", ".sql", ".csv", ".env.example"}


class WorkspaceError(ValueError):
    pass


def normalize_path(path: str) -> str:
    p = posixpath.normpath(path.strip().lstrip("/"))
    if not _SAFE_PATH.match(p) or p.startswith("..") or "/../" in f"/{p}/" or p in (".", ""):
        raise WorkspaceError(f"invalid file path: {path!r}")
    if any(seg.startswith(".") and seg not in (".gitignore", ".env.example") for seg in p.split("/")):
        raise WorkspaceError(f"hidden files are not allowed: {path!r}")
    return p


def workspace_rows(db: Session, project_id: uuid.UUID) -> list[ProjectFile]:
    return list(db.scalars(select(ProjectFile).where(ProjectFile.project_id == project_id, ProjectFile.kind == "workspace").order_by(ProjectFile.path)))


def write_file(db: Session, project: Project, path: str, content: bytes, user_id: uuid.UUID | None) -> ProjectFile:
    path = normalize_path(path)
    if len(content) > MAX_FILE_BYTES:
        raise WorkspaceError(f"{path} exceeds {MAX_FILE_BYTES} bytes")
    rows = workspace_rows(db, project.id)
    existing = next((r for r in rows if r.path == path), None)
    if existing is None and len(rows) >= MAX_FILES:
        raise WorkspaceError(f"workspace file limit ({MAX_FILES}) reached")
    total = sum(r.size_bytes for r in rows if r.path != path) + len(content)
    if total > MAX_TOTAL_BYTES:
        raise WorkspaceError("workspace size limit reached")
    sha = hashlib.sha256(content).hexdigest()
    key = f"orgs/{project.org_id}/projects/{project.id}/workspace/{sha}"
    get_storage().put_bytes(key, content, guess_mime(path))
    if existing:
        existing.storage_key, existing.size_bytes, existing.sha256, existing.mime_type = key, len(content), sha, guess_mime(path)
        return existing
    f = ProjectFile(
        org_id=project.org_id, project_id=project.id, kind="workspace", path=path, storage_key=key, size_bytes=len(content),
        mime_type=guess_mime(path), sha256=sha, uploaded_by=user_id,
    )  # fmt: skip
    db.add(f)
    db.flush()
    return f


def delete_file(db: Session, project: Project, path: str) -> bool:
    path = normalize_path(path)
    row = db.scalar(select(ProjectFile).where(ProjectFile.project_id == project.id, ProjectFile.kind == "workspace", ProjectFile.path == path))
    if row is None:
        return False
    db.delete(row)  # content-addressed blobs may be shared by snapshots; they are not deleted here
    return True


def init_from_template(db: Session, project: Project, template_key: str, user_id: uuid.UUID | None) -> int:
    t = PROJECT_TEMPLATES.get(template_key)
    if t is None:
        raise WorkspaceError(f"unknown template '{template_key}'")
    for path, content in t.files.items():
        write_file(db, project, path, content.encode(), user_id)
    project.builder_template = template_key
    return len(t.files)


def read_files(db: Session, project_id: uuid.UUID) -> dict[str, bytes]:
    storage = get_storage()
    return {r.path: storage.get_bytes(r.storage_key) for r in workspace_rows(db, project_id)}


def files_digest(rows: list[ProjectFile]) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: r.path):
        h.update(f"{r.path}\0{r.sha256}\n".encode())
    return h.hexdigest()


def export_zip(files: dict[str, bytes], root: str = "project") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in sorted(files.items()):
            z.writestr(f"{root}/{path}", data)
    return buf.getvalue()


def snapshot(db: Session, project: Project, user_id: uuid.UUID | None, label: str) -> uuid.UUID:
    """Store the current workspace as a ZIP artifact version (used for undo and export history)."""
    import tempfile
    from pathlib import Path

    files = read_files(db, project.id)
    with tempfile.TemporaryDirectory(prefix="mind-snap-") as tmp:
        p = Path(tmp) / f"{re.sub(r'[^A-Za-z0-9]+', '-', project.name).strip('-') or 'project'}.zip"
        p.write_bytes(export_zip(files))
        art = create_artifact(db, org_id=project.org_id, project_id=project.id, user_id=user_id, kind="code", title=f"{project.name}: {label}", source={"type": "workspace_snapshot", "label": label})
        store_version(db, art, [(p, {"format": "zip", "files": len(files)})], validation={"files": len(files)}, params=None, user_id=user_id)
        art.status = "completed"
        return art.id


def restore_snapshot(db: Session, project: Project, artifact_id: uuid.UUID, user_id: uuid.UUID | None) -> int:
    from ..models import GeneratedArtifact

    art = db.get(GeneratedArtifact, artifact_id)
    if art is None or art.project_id != project.id or art.kind != "code" or not art.versions:
        raise WorkspaceError("snapshot not found")
    data = get_storage().get_bytes(art.versions[-1].files[0]["storage_key"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        entries = {n.split("/", 1)[1]: z.read(n) for n in z.namelist() if "/" in n and not n.endswith("/")}
    for r in workspace_rows(db, project.id):
        if r.path not in entries:
            db.delete(r)
    for path, content in entries.items():
        write_file(db, project, path, content, user_id)
    return len(entries)


# ---------------------------------------------------------------------------- tests in sandbox


def run_tests(files: dict[str, bytes], template_key: str | None) -> dict[str, Any]:
    t = PROJECT_TEMPLATES.get(template_key or "")
    if t is None:
        return {"ran": False, "reason": "project has no runnable template configured"}
    if not (get_settings().sandbox_enabled and docker_available()):
        return {"ran": False, "reason": "Docker sandbox unavailable"}
    try:
        wd = materialize(files)
        r = run(wd, list(t.test_command), runtime=t.runtime, limits=Limits(timeout_s=90))
    except SandboxError as exc:
        return {"ran": False, "reason": str(exc)}
    return {"ran": True, "passed": r.ok, "exit_code": r.exit_code, "timed_out": r.timed_out, "stdout": r.stdout[-6000:], "stderr": r.stderr[-6000:], "command": list(t.test_command), "duration_s": r.duration_s}


@handler("builder.test")
def builder_test(ctx: JobContext) -> dict[str, Any]:
    with session_scope() as db:
        project = db.get(Project, ctx.project_id)
        if project is None:
            raise JobFailed("project not found")
        files, template = read_files(db, project.id), project.builder_template
    ctx.progress(20, "running tests in sandbox")
    res = run_tests(files, template)
    if not res.get("ran"):
        raise JobFailed(f"tests could not run: {res.get('reason')}", {"tests": res})
    return {"tests": res, "_status": "completed" if res["passed"] else "failed"}


# ---------------------------------------------------------------------------- AI edits


BUILDER_SYSTEM = """You are the MIND Coding Agent editing a small web project that runs in an OFFLINE sandbox.
Return ONLY JSON: {"summary": "what you changed", "files": [{"path": "relative/path", "content": "full new file content"}], "delete": ["path", ...]}
Rules:
- Return complete file contents for every file you create or change. Do not return unchanged files.
- Runtime: {runtime}. No network access: do not add dependencies beyond the standard library{deps}.
- The app is served under a path prefix: use RELATIVE URLs (e.g. fetch('api/items'), href="styles.css"), never leading-slash URLs.
- Keep the existing tests passing and add or update tests for new behaviour. Test command: {test_cmd}
- Never include secrets. Keep files under 200 KB."""

PREINSTALLED = {"python": " and the preinstalled packages fastapi, uvicorn, jinja2, sqlalchemy, pydantic, pytest, httpx, numpy", "node": ""}


def _context_files(files: dict[str, bytes], budget: int = 60_000) -> str:
    parts, used = [], 0
    for path, data in sorted(files.items()):
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if ext not in TEXT_EXT or len(data) > 200_000:
            parts.append(f"--- {path} (binary or large file, {len(data)} bytes, not shown)")
            continue
        text = data.decode(errors="replace")
        if used + len(text) > budget:
            parts.append(f"--- {path} (omitted: context budget)")
            continue
        used += len(text)
        parts.append(f"--- {path}\n{text}")
    return "\n".join(parts)


def _apply_changes(db: Session, project: Project, change: dict[str, Any], user_id: uuid.UUID | None) -> list[str]:
    if not isinstance(change, dict) or not isinstance(change.get("files", []), list):
        raise WorkspaceError("model output is not a valid change set")
    touched = []
    for f in change.get("files", []):
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("content"), str):
            raise WorkspaceError("each file needs a string path and content")
        write_file(db, project, f["path"], f["content"].encode(), user_id)
        touched.append(normalize_path(f["path"]))
    for p in change.get("delete", []) or []:
        if isinstance(p, str) and delete_file(db, project, p):
            touched.append(f"deleted:{normalize_path(p)}")
    return touched


@handler("builder.ai_edit")
def builder_ai_edit(ctx: JobContext) -> dict[str, Any]:
    prompt = ctx.payload["prompt"]
    with session_scope() as db:
        project = db.get(Project, ctx.project_id)
        if project is None:
            raise JobFailed("project not found")
        t = PROJECT_TEMPLATES.get(project.builder_template or "")
        if t is None:
            raise JobFailed("initialise the project from a Builder template first")
        snap_id = snapshot(db, project, ctx.user_id, "before AI edit")
        files = read_files(db, project.id)
    system = (
        BUILDER_SYSTEM.replace("{runtime}", t.runtime).replace("{deps}", PREINSTALLED.get(t.runtime, "")).replace("{test_cmd}", " ".join(t.test_command))
    )
    ctx.progress(10, "Coding Agent is writing changes")
    history: list[dict[str, Any]] = []
    request = f"Project files:\n{_context_files(files)}\n\nRequested change:\n{prompt}"
    tests: dict[str, Any] = {}
    summary = ""
    for attempt in range(2):
        try:
            r = llm.complete_sync(ctx.org_id, ctx.user_id, system, request, max_output_tokens=12000, purpose="builder")
        except llm.LLMUnavailable as exc:
            raise JobFailed(str(exc), {"snapshot_artifact_id": str(snap_id)}) from exc
        try:
            change = llm.extract_json(r.text)
            with session_scope() as db:
                project = db.get(Project, ctx.project_id)
                assert project is not None
                touched = _apply_changes(db, project, change, ctx.user_id)
                files = read_files(db, project.id)
        except (ValueError, WorkspaceError) as exc:
            history.append({"attempt": attempt + 1, "model": r.model, "error": f"unusable model output: {exc}"})
            request += f"\n\nYour previous reply could not be applied ({exc}). Reply with valid JSON only."
            continue
        summary = str(change.get("summary", ""))[:2000]
        ctx.progress(50 + attempt * 20, "Testing Agent is running the test suite")
        tests = run_tests(files, project.builder_template)
        history.append({"attempt": attempt + 1, "model": r.model, "summary": summary, "files": touched, "tests_passed": tests.get("passed"), "tests_ran": tests.get("ran")})
        if not tests.get("ran") or tests.get("passed"):
            break
        request = (
            f"Project files after your change:\n{_context_files(files)}\n\nOriginal request:\n{prompt}\n\n"
            f"The test command failed:\nSTDOUT:\n{tests.get('stdout', '')[-3000:]}\nSTDERR:\n{tests.get('stderr', '')[-3000:]}\nFix the code (or the test if the test is wrong)."
        )
        ctx.progress(60, "Coding Agent is repairing a failing test")
    if not history or "files" not in history[-1]:
        raise JobFailed("the model did not return an applicable change", {"attempts": history, "snapshot_artifact_id": str(snap_id)})
    ok = bool(tests.get("passed"))
    return {
        "summary": summary,
        "attempts": history,
        "tests": tests,
        "snapshot_artifact_id": str(snap_id),
        "_status": "completed" if ok else "partially_completed",
    }


def template_catalog() -> list[dict[str, Any]]:
    return [
        {"key": t.key, "title": t.title, "description": t.description, "runtime": t.runtime, "files": sorted(t.files), "test_command": list(t.test_command)}
        for t in PROJECT_TEMPLATES.values()
    ]


def as_json(obj: Any) -> str:
    return json.dumps(obj, default=str)
