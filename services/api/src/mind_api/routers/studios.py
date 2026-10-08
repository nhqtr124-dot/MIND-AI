"""MIND 3D, Documents, Image and Research studio endpoints."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from mind_ai import ADAPTERS
from mind_cad import TEMPLATES, template_catalog, validate_mesh
from mind_docs import DocumentSpec, PresentationSpec, WorkbookSpec
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from ..deps import DB, CurrentUser, rate_limit, resolve_scope
from ..jobs import enqueue
from ..models import IntegrationCredential, ModelConfig, ProjectFile
from ..schemas import (
    ArtifactOut,
    CadGenerateIn,
    DocumentFromPromptIn,
    DocumentGenerateIn,
    ImageEditIn,
    ImageGenerateIn,
    IntegrationCreate,
    IntegrationOut,
    JobCreated,
    ResearchCreate,
    TextToCadIn,
    TextToCadOut,
)
from ..security import encrypt_secret, secret_hint
from ..services import llm
from ..services.artifacts import artifact_dict, create_artifact, store_version
from ..services.audit import audit
from ..services.image_service import EditRequest, edit_image_bytes
from ..storage import get_storage
from .artifacts import get_artifact

router = APIRouter(tags=["studios"])
GEN_LIMIT = rate_limit("generate", 30)


# ============================================================================ CAD


@router.get("/cad/templates")
def cad_templates() -> list[dict[str, Any]]:
    return template_catalog()


@router.post("/cad/generate", response_model=JobCreated, status_code=202, dependencies=[GEN_LIMIT])
def cad_generate(body: CadGenerateIn, user: CurrentUser, db: DB) -> JobCreated:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    t = TEMPLATES.get(body.template)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown template '{body.template}'")
    try:
        t.Params.model_validate(body.params)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, json.loads(exc.json(include_url=False))
        ) from exc
    if body.artifact_id:
        art = get_artifact(db, user, body.artifact_id, "editor")
        if art.kind != "cad" or art.org_id != scope.org_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Can only add versions to a CAD artifact in the same organization"
            )
        art.status = "running"
    else:
        art = create_artifact(
            db,
            org_id=scope.org_id,
            project_id=scope.project_id,
            user_id=user.id,
            kind="cad",
            title=body.title or t.title,
            source={"template": body.template},
        )
    job = enqueue(db, "cad.generate", org_id=scope.org_id, user_id=user.id, project_id=scope.project_id,
                  payload={"artifact_id": str(art.id), "template": body.template, "params": body.params, "formats": body.formats}, max_attempts=1)  # fmt: skip
    return JobCreated(job_id=job.id, artifact_id=art.id)


TEXT_TO_CAD_SYSTEM = """You are the MIND CAD Agent. Map the user's request onto ONE of the parametric templates below, choosing parameter values in millimetres.
Return ONLY JSON: {"template": "<key or null>", "params": {...}, "explanation": "...", "assumptions": ["..."], "missing_information": ["..."]}
- Only use parameters defined in the template's JSON schema; omit parameters to keep defaults.
- If no template fits, return "template": null and explain what is unsupported.
- Never invent dimensions you cannot know: list them under missing_information and state the assumed value under assumptions.
- A single reference image cannot give accurate hidden dimensions; say so if relevant.

Templates:
"""


@router.post("/cad/text-to-cad", response_model=TextToCadOut, dependencies=[GEN_LIMIT])
async def text_to_cad(body: TextToCadIn, user: CurrentUser, db: DB) -> TextToCadOut:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    catalog = json.dumps(
        [
            {"key": t["key"], "description": t["description"], "params_schema": t["params_schema"]}
            for t in template_catalog()
        ]
    )
    db.commit()
    try:
        r = await llm.complete(
            scope.org_id,
            user.id,
            TEXT_TO_CAD_SYSTEM + catalog,
            body.prompt,
            max_output_tokens=2000,
            purpose="cad",
        )
    except llm.LLMUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    try:
        data = llm.extract_json(r.text)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"The model returned no usable JSON ({r.model})"
        ) from exc
    key = data.get("template")
    params = data.get("params") or {}
    supported = key in TEMPLATES
    explanation = str(data.get("explanation", ""))
    if supported:
        try:
            params = TEMPLATES[key].Params.model_validate(params).model_dump()
        except ValidationError as exc:
            supported, explanation = (
                False,
                f"{explanation}\nProposed parameters were invalid: {exc.errors()[:3]}",
            )
    return TextToCadOut(
        template=key if supported else None, params=params if supported else {}, explanation=explanation,
        assumptions=[str(a) for a in data.get("assumptions", [])], missing_information=[str(a) for a in data.get("missing_information", [])],
        supported=supported, model=r.model,
    )  # fmt: skip


class MeshValidateIn(BaseModel):
    file_id: uuid.UUID
    expected_extents: tuple[float, float, float] | None = None
    tolerance: float = 0.5


@router.post("/cad/validate")
def validate_uploaded_mesh(body: MeshValidateIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    f = db.get(ProjectFile, body.file_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    resolve_scope(db, user, f.org_id, f.project_id)
    if Path(f.path).suffix.lower() not in (".stl", ".3mf", ".obj"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Mesh validation supports STL, 3MF and OBJ"
        )
    with tempfile.TemporaryDirectory(prefix="mind-val-") as tmp:
        p = get_storage().download_to(f.storage_key, Path(tmp) / f.path)
        return validate_mesh(p, expected_extents=body.expected_extents, tolerance=body.tolerance).to_dict()


# ============================================================================ Documents


def _kind_spec(kind: str) -> type[BaseModel]:
    return {"document": DocumentSpec, "presentation": PresentationSpec, "spreadsheet": WorkbookSpec}[kind]


def _check_format(kind: str, fmt: str) -> None:
    ok = {
        "document": {"docx", "pdf", "md", "html", "txt"},
        "presentation": {"pptx"},
        "spreadsheet": {"xlsx", "csv"},
    }[kind]
    if fmt not in ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{kind} supports formats: {sorted(ok)}")


def _enqueue_doc(
    db: DB, user: CurrentUser, scope: Any, kind: str, fmt: str, spec: dict[str, Any], title: str
) -> JobCreated:
    art = create_artifact(
        db,
        org_id=scope.org_id,
        project_id=scope.project_id,
        user_id=user.id,
        kind=kind,
        title=title,
        source={"format": fmt},
    )
    job = enqueue(db, "documents.generate", org_id=scope.org_id, user_id=user.id, project_id=scope.project_id,
                  payload={"artifact_id": str(art.id), "kind": kind, "format": fmt, "spec": spec}, max_attempts=1)  # fmt: skip
    return JobCreated(job_id=job.id, artifact_id=art.id)


@router.post("/documents/generate", response_model=JobCreated, status_code=202, dependencies=[GEN_LIMIT])
def documents_generate(body: DocumentGenerateIn, user: CurrentUser, db: DB) -> JobCreated:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    _check_format(body.kind, body.format)
    try:
        spec = _kind_spec(body.kind).model_validate(body.spec)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, json.loads(exc.json(include_url=False))
        ) from exc
    title = body.title or getattr(spec, "title", None) or "Spreadsheet"
    return _enqueue_doc(db, user, scope, body.kind, body.format, body.spec, title)


DOC_SYSTEM = """You are the MIND Document Agent. Produce a structured specification for a {kind} that answers the user's request.
Return ONLY JSON matching this JSON schema:
{schema}
Rules: be factual; if source documents are provided, use only facts from them and say when information is missing.
For spreadsheets use real Excel formulas (strings starting with '=') for computed cells, and fill "expected" with the values those formulas must produce."""


@router.post("/documents/from-prompt", response_model=JobCreated, status_code=202, dependencies=[GEN_LIMIT])
async def documents_from_prompt(body: DocumentFromPromptIn, user: CurrentUser, db: DB) -> JobCreated:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    _check_format(body.kind, body.format)
    sources = []
    for fid in body.source_file_ids:
        f = db.get(ProjectFile, fid)
        if f is None or f.org_id != scope.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Source file not found")
        resolve_scope(db, user, f.org_id, f.project_id)
        sources.append(f'<document name="{f.path}">\n{(f.extracted_text or "")[:60000]}\n</document>')
    model = _kind_spec(body.kind)
    system = DOC_SYSTEM.replace("{kind}", body.kind).replace(
        "{schema}", json.dumps(model.model_json_schema())
    )
    prompt = ("\n\n".join(sources) + "\n\n" if sources else "") + f"Request: {body.prompt}"
    db.commit()
    try:
        r = await llm.complete(
            scope.org_id, user.id, system, prompt, max_output_tokens=8000, purpose="documents"
        )
    except llm.LLMUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    try:
        spec_obj = model.model_validate(llm.extract_json(r.text))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"The model's document outline was not valid ({r.model}): {str(exc)[:300]}",
        ) from exc
    spec = spec_obj.model_dump()
    title = getattr(spec_obj, "title", None) or body.prompt[:80]
    return _enqueue_doc(db, user, scope, body.kind, body.format, spec, title)


# ============================================================================ Images


@router.post("/images/generate", response_model=JobCreated, status_code=202, dependencies=[GEN_LIMIT])
def images_generate(body: ImageGenerateIn, user: CurrentUser, db: DB) -> JobCreated:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    m = db.get(ModelConfig, body.model_config_id)
    if m is None or m.org_id != scope.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    if not m.enabled or "image_generation" not in m.capabilities:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That model is not enabled for image generation"
        )
    if not ADAPTERS[m.provider.kind].supports_images:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Image generation is not implemented for {m.provider.kind} providers yet",
        )
    art = create_artifact(
        db,
        org_id=scope.org_id,
        project_id=scope.project_id,
        user_id=user.id,
        kind="image",
        title=body.prompt[:120],
        source={"model": m.model_name, "provider": m.provider.kind},
    )
    job = enqueue(db, "image.generate", org_id=scope.org_id, user_id=user.id, project_id=scope.project_id,
                  payload={"artifact_id": str(art.id), "model_config_id": str(m.id), "prompt": body.prompt, "size": body.size, "n": body.n}, max_attempts=2)  # fmt: skip
    return JobCreated(job_id=job.id, artifact_id=art.id)


@router.post("/images/edit", response_model=ArtifactOut, dependencies=[GEN_LIMIT])
def images_edit(body: ImageEditIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    """Local, deterministic edits with Pillow (resize, crop, rotate, flip, adjust, convert)."""
    if body.source_file_id:
        f = db.get(ProjectFile, body.source_file_id)
        if f is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        scope = resolve_scope(db, user, f.org_id, f.project_id, "editor")
        key, name = f.storage_key, f.path
    elif body.source_artifact_id:
        a = get_artifact(db, user, body.source_artifact_id, "editor")
        scope = resolve_scope(db, user, a.org_id, a.project_id, "editor")
        files = a.versions[-1].files if a.versions else []
        f2 = (
            next((x for x in files if x["name"] == body.source_file_name), None)
            if body.source_file_name
            else next((x for x in files if x["mime_type"].startswith("image/")), None)
        )
        if f2 is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Image file not found in artifact")
        key, name = f2["storage_key"], f2["name"]
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "source_file_id or source_artifact_id is required"
        )
    try:
        req = EditRequest.model_validate({"ops": body.ops, "output_format": body.output_format})
        out, info = edit_image_bytes(get_storage().get_bytes(key), req)
    except (ValidationError, ValueError, OSError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Image edit failed: {exc}") from exc
    art = create_artifact(db, org_id=scope.org_id, project_id=body.project_id or scope.project_id, user_id=user.id, kind="image",
                          title=body.title or f"Edited {name}", source={"edited_from": name, "ops": body.ops})  # fmt: skip
    ext = "jpg" if body.output_format == "jpeg" else body.output_format
    with tempfile.TemporaryDirectory(prefix="mind-imgedit-") as tmp:
        p = Path(tmp) / f"{Path(name).stem}-edited.{ext}"
        p.write_bytes(out)
        store_version(
            db,
            art,
            [(p, {**info, "format": ext, "image_format": info["format"]})],
            validation={"decoded": True, **info},
            params={"ops": body.ops},
            user_id=user.id,
        )
    art.status, art.validation_status = "completed", "decoded"
    return artifact_dict(art)


# ============================================================================ Research & integrations


@router.get("/integrations", response_model=list[IntegrationOut])
def list_integrations(org_id: uuid.UUID, user: CurrentUser, db: DB) -> list[IntegrationCredential]:
    resolve_scope(db, user, org_id, None)
    return list(
        db.scalars(
            select(IntegrationCredential)
            .where(IntegrationCredential.org_id == org_id)
            .order_by(IntegrationCredential.created_at)
        )
    )


@router.post("/integrations", response_model=IntegrationOut, status_code=201)
def create_integration(body: IntegrationCreate, user: CurrentUser, db: DB) -> IntegrationCredential:
    resolve_scope(db, user, body.org_id, None, "admin")
    if body.kind in ("brave", "tavily") and not body.secret:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{body.kind} requires an API key")
    if body.kind == "searxng" and not body.base_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "SearXNG requires the instance base URL")
    c = IntegrationCredential(
        org_id=body.org_id, kind=body.kind, name=body.name, base_url=body.base_url,
        secret_encrypted=encrypt_secret(body.secret) if body.secret else None, secret_hint=secret_hint(body.secret) if body.secret else None, created_by=user.id,
    )  # fmt: skip
    db.add(c)
    db.flush()
    audit(
        db,
        "integration.create",
        user_id=user.id,
        org_id=body.org_id,
        target_type="integration",
        target_id=c.id,
        kind=body.kind,
    )
    return c


@router.delete("/integrations/{integration_id}", status_code=204)
def delete_integration(integration_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    c = db.get(IntegrationCredential, integration_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found")
    resolve_scope(db, user, c.org_id, None, "admin")
    db.delete(c)
    audit(
        db,
        "integration.delete",
        user_id=user.id,
        org_id=c.org_id,
        target_type="integration",
        target_id=integration_id,
    )


@router.post("/research", response_model=JobCreated, status_code=202, dependencies=[GEN_LIMIT])
def start_research(body: ResearchCreate, user: CurrentUser, db: DB) -> JobCreated:
    scope = resolve_scope(db, user, body.org_id, body.project_id, "editor")
    for u in body.urls:
        if not u.startswith(("http://", "https://")):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not an http(s) URL: {u}")
    art = create_artifact(
        db,
        org_id=scope.org_id,
        project_id=scope.project_id,
        user_id=user.id,
        kind="research_report",
        title=body.question[:200],
        source={"question": body.question},
    )
    job = enqueue(db, "research.run", org_id=scope.org_id, user_id=user.id, project_id=scope.project_id,
                  payload={"artifact_id": str(art.id), "question": body.question, "urls": body.urls, "max_sources": body.max_sources, "engine": body.engine}, max_attempts=1)  # fmt: skip
    return JobCreated(job_id=job.id, artifact_id=art.id)
