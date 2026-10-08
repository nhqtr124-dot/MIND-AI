"""CAD job: parametric generation -> STL/STEP/3MF -> validation -> versioned artifact."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from mind_cad import CadError, PrintSettings, generate_part

from ..db import session_scope
from ..jobs import JobContext, JobFailed, handler
from ..models import GeneratedArtifact
from .artifacts import store_version


@handler("cad.generate")
def cad_generate(ctx: JobContext) -> dict[str, Any]:
    p = ctx.payload
    artifact_id = uuid.UUID(p["artifact_id"])
    ctx.progress(5, "building geometry")
    settings = PrintSettings(**p.get("print_settings", {})) if p.get("print_settings") else None
    with tempfile.TemporaryDirectory(prefix="mind-cad-") as tmp:
        try:
            res = generate_part(
                p["template"],
                p.get("params", {}),
                Path(tmp),
                formats=tuple(p.get("formats", ["stl", "step"])),
                settings=settings,
            )
        except CadError as exc:
            with session_scope() as db:
                a = db.get(GeneratedArtifact, artifact_id)
                if a:
                    a.status, a.validation_status = "failed", "generation_failed"
            raise JobFailed(f"CAD generation failed: {exc}") from exc
        ctx.progress(70, "validating meshes")
        report = res.to_dict()
        paths = [(Path(f.path), {"format": f.format, "part": f.part}) for f in res.files]
        paths.append((Path(tmp) / "report.json", {"format": "json", "part": "report"}))
        with session_scope() as db:
            a = db.get(GeneratedArtifact, artifact_id)
            if a is None:
                raise JobFailed("artifact record disappeared")
            # Paths in the report point at the temp dir; drop them before persisting.
            for f in report["files"]:
                f.pop("path", None)
            store_version(db, a, paths, validation=report, params=res.params, user_id=ctx.user_id)
            a.validation_status = res.status
            # A part that failed validation is a completed generation whose output is NOT print-ready.
            a.status = "completed" if res.status != "validation_failed" else "partially_completed"
    ctx.progress(100, res.status)
    return {
        "artifact_id": str(artifact_id),
        "status": res.status,
        "files": [f.format for f in res.files],
        "bom": res.bom,
    }
