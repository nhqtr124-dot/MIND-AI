"""Generated artifacts: versioned files in object storage with validation reports."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..models import ArtifactVersion, GeneratedArtifact
from ..security import sign_payload
from ..storage import get_storage, sha256_file

EXTRA_TYPES = {
    ".stl": "model/stl",
    ".step": "model/step",
    ".3mf": "model/3mf",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def guess_mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    return EXTRA_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def create_artifact(
    db: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    kind: str,
    title: str,
    source: dict[str, Any] | None = None,
    status: str = "running",
) -> GeneratedArtifact:
    a = GeneratedArtifact(
        org_id=org_id,
        project_id=project_id,
        created_by=user_id,
        kind=kind,
        title=title[:200],
        status=status,
        source=source or {},
    )
    db.add(a)
    db.flush()
    return a


def store_version(
    db: Session,
    artifact: GeneratedArtifact,
    paths: list[tuple[Path, dict[str, Any]]],
    *,
    validation: dict[str, Any] | None,
    params: dict[str, Any] | None,
    user_id: uuid.UUID | None,
) -> ArtifactVersion:
    """Upload files that exist on disk and record them as a new version.

    Each file must exist and be non-empty; otherwise nothing is recorded.
    """
    storage = get_storage()
    version = artifact.current_version + 1
    files: list[dict[str, Any]] = []
    for path, meta in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"refusing to record missing or empty file {path.name}")
        key = f"orgs/{artifact.org_id}/artifacts/{artifact.id}/v{version}/{path.name}"
        mime = guess_mime(path.name)
        storage.put_file(key, path, mime)
        files.append(
            {
                "name": path.name,
                "storage_key": key,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "mime_type": mime,
                **meta,
            }
        )
    v = ArtifactVersion(
        artifact_id=artifact.id,
        version=version,
        files=files,
        validation=validation,
        params=params,
        created_by=user_id,
    )
    db.add(v)
    artifact.current_version = version
    db.flush()
    return v


def download_token(org_id: uuid.UUID, storage_key: str, filename: str, mime: str, ttl_s: int = 3600) -> str:
    return sign_payload("dl", {"o": str(org_id), "k": storage_key, "n": filename, "m": mime}, ttl_s)


def artifact_dict(a: GeneratedArtifact, include_versions: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(a.id),
        "org_id": str(a.org_id),
        "project_id": str(a.project_id) if a.project_id else None,
        "kind": a.kind,
        "title": a.title,
        "status": a.status,
        "validation_status": a.validation_status,
        "source": a.source,
        "current_version": a.current_version,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }
    if include_versions:
        d["versions"] = [
            {
                "version": v.version,
                "files": [{k: f[k] for k in f if k != "storage_key"} for f in v.files],
                "validation": v.validation,
                "params": v.params,
                "created_at": v.created_at.isoformat(),
            }
            for v in a.versions
        ]
    return d
