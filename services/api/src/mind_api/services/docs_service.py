"""Document job: spec -> real file -> reopen and validate -> versioned artifact."""

from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from mind_docs import (
    DocumentSpec,
    PresentationSpec,
    WorkbookSpec,
    convert_with_libreoffice,
    libreoffice_available,
    render_csv,
    render_document,
    render_pdf_previews,
    render_pptx,
    render_xlsx,
    validate_csv,
    validate_document,
    validate_pptx,
    validate_xlsx,
)

from ..db import session_scope
from ..jobs import JobContext, JobFailed, handler
from ..models import GeneratedArtifact
from .artifacts import store_version


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60] or "document"


def build_document(kind: str, fmt: str, spec: dict[str, Any], out_dir: Path) -> tuple[Path, dict[str, Any], list[Path]]:
    """Render and validate; returns (file, validation report, extra preview files)."""
    previews: list[Path] = []
    if kind == "document":
        ds = DocumentSpec.model_validate(spec)
        path = render_document(ds, fmt, out_dir / f"{_slug(ds.title)}.{fmt}")
        report = validate_document(path, fmt, ds).to_dict()
    elif kind == "presentation":
        ps = PresentationSpec.model_validate(spec)
        path = render_pptx(ps, out_dir / f"{_slug(ps.title)}.pptx")
        report = validate_pptx(path, ps).to_dict()
    elif kind == "spreadsheet":
        ws = WorkbookSpec.model_validate(spec)
        name = _slug(spec.get("title") or ws.sheets[0].name)
        if fmt == "csv":
            rows = ws.sheets[0].rows
            path = render_csv([str(c) for c in rows[0]], [list(r) for r in rows[1:]], out_dir / f"{name}.csv")
            report = validate_csv(path, [str(c) for c in rows[0]], len(rows) - 1).to_dict()
        else:
            path = render_xlsx(ws, out_dir / f"{name}.xlsx")
            report = validate_xlsx(path, ws).to_dict()
    else:
        raise JobFailed(f"unknown document kind '{kind}'")

    # Visual previews: PDFs directly, office files via LibreOffice when installed.
    try:
        pdf = path if path.suffix == ".pdf" else None
        if pdf is None and path.suffix in (".docx", ".pptx", ".xlsx") and libreoffice_available():
            pdf = convert_with_libreoffice(path, "pdf", out_dir / "preview")
        if pdf is not None:
            for i, png in enumerate(render_pdf_previews(pdf, max_pages=3, zoom=0.8)):
                p = out_dir / f"preview-{i + 1}.png"
                p.write_bytes(png)
                previews.append(p)
            report["preview_pages"] = len(previews)
    except Exception as exc:  # noqa: BLE001 - previews are best-effort; record why they are missing
        report["preview_error"] = str(exc)[:300]
    return path, report, previews


@handler("documents.generate")
def documents_generate(ctx: JobContext) -> dict[str, Any]:
    p = ctx.payload
    artifact_id = uuid.UUID(p["artifact_id"])
    ctx.progress(10, "rendering")
    with tempfile.TemporaryDirectory(prefix="mind-doc-") as tmp:
        try:
            path, report, previews = build_document(p["kind"], p["format"], p["spec"], Path(tmp))
        except JobFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - spec/render errors are user-facing failures
            with session_scope() as db:
                a = db.get(GeneratedArtifact, artifact_id)
                if a:
                    a.status, a.validation_status = "failed", "generation_failed"
            raise JobFailed(f"document generation failed: {exc}") from exc
        ctx.progress(70, "validated; storing")
        with session_scope() as db:
            a = db.get(GeneratedArtifact, artifact_id)
            if a is None:
                raise JobFailed("artifact record disappeared")
            files = [(path, {"format": p["format"], "role": "document"})] + [(pv, {"format": "png", "role": "preview"}) for pv in previews]
            store_version(db, a, files, validation=report, params={"kind": p["kind"], "format": p["format"]}, user_id=ctx.user_id)
            a.validation_status = "passed" if report["passed"] else "failed"
            a.status = "completed" if report["passed"] else "failed"
    if not report["passed"]:
        raise JobFailed("generated file failed validation", {"validation": report})
    return {"artifact_id": str(artifact_id), "validation": report}
