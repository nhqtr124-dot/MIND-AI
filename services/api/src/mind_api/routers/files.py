from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from mind_docs import ExtractionError, extract_text
from sqlalchemy import select

from ..config import get_settings
from ..deps import DB, CurrentUser, client_ip, rate_limit, require_org, require_project, resolve_scope
from ..models import ProjectFile
from ..schemas import FileOut
from ..services.artifacts import guess_mime
from ..services.audit import audit
from ..storage import get_storage

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_EXT = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".json", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".stl", ".step", ".stp", ".3mf", ".obj",
    ".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".webm", ".mov", ".py", ".js", ".ts", ".tsx", ".css", ".yaml", ".yml", ".zip",
}  # fmt: skip
# Magic-byte checks for types where a mismatch would be dangerous or misleading.
MAGIC = {
    ".pdf": [b"%PDF"],
    ".png": [b"\x89PNG"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF8"],
    ".webp": [b"RIFF"],
    ".docx": [b"PK\x03\x04"],
    ".pptx": [b"PK\x03\x04"],
    ".xlsx": [b"PK\x03\x04"],
    ".3mf": [b"PK\x03\x04"],
    ".zip": [b"PK\x03\x04"],
}


def safe_name(name: str) -> str:
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._\- ]+", "_", base).strip(" .")[:150]
    return base or "upload"


def scan_for_malware(path: Path) -> tuple[bool | None, str]:
    """Scan with ClamAV when installed. Returns (clean, detail); clean is None if not scanned."""
    exe = shutil.which("clamdscan") or shutil.which("clamscan")
    if not exe:
        return None, "not scanned (ClamAV not installed)"
    proc = subprocess.run([exe, "--no-summary", str(path)], capture_output=True, timeout=120, check=False)
    if proc.returncode == 0:
        return True, "ClamAV: clean"
    if proc.returncode == 1:
        return False, "ClamAV: " + proc.stdout.decode(errors="replace").strip()[-300:]
    return None, "ClamAV error: " + proc.stderr.decode(errors="replace").strip()[-300:]


def file_out(f: ProjectFile) -> FileOut:
    return FileOut.model_validate({**{c: getattr(f, c) for c in FileOut.model_fields if hasattr(f, c)}, "has_text": bool(f.extracted_text)})


@router.post("", response_model=FileOut, status_code=201, dependencies=[rate_limit("upload", 60)])
def upload(
    request: Request,
    user: CurrentUser,
    db: DB,
    file: UploadFile = File(...),
    org_id: uuid.UUID | None = Form(None),
    project_id: uuid.UUID | None = Form(None),
) -> FileOut:
    scope = resolve_scope(db, user, org_id, project_id, "editor")
    name = safe_name(file.filename or "upload")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"File type '{ext or 'none'}' is not allowed")
    limit = get_settings().max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="mind-up-") as tmp:
        path = Path(tmp) / name
        size, h = 0, hashlib.sha256()
        with path.open("wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds {get_settings().max_upload_mb} MB")
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is empty")
        head = path.read_bytes()[:16]
        if ext in MAGIC and not any(head.startswith(m) for m in MAGIC[ext]):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"File content does not match its '{ext}' extension")
        clean, scan_detail = scan_for_malware(path)
        if clean is False:
            audit(db, "file.malware_blocked", user_id=user.id, org_id=scope.org_id, ip=client_ip(request), name=name, detail=scan_detail)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File was rejected by the malware scanner")
        text, err = None, None
        try:
            text = extract_text(path)
        except ExtractionError as exc:
            err = str(exc)
        except Exception as exc:  # noqa: BLE001 - corrupt documents must not break uploads
            err = f"could not read file: {type(exc).__name__}"
        fid = uuid.uuid4()
        key = f"orgs/{scope.org_id}/uploads/{fid}/{name}"
        mime = guess_mime(name)
        get_storage().put_file(key, path, mime)
    f = ProjectFile(
        id=fid, org_id=scope.org_id, project_id=scope.project_id, kind="upload", path=name, storage_key=key, size_bytes=size,
        mime_type=mime, sha256=h.hexdigest(), extracted_text=text.replace("\x00", "") if text else None, extraction_error=err, uploaded_by=user.id,
    )  # fmt: skip
    db.add(f)
    db.flush()
    audit(db, "file.upload", user_id=user.id, org_id=scope.org_id, target_type="file", target_id=fid, ip=client_ip(request), name=name, size=size, scan=scan_detail)
    return file_out(f)


@router.get("", response_model=list[FileOut])
def list_files(user: CurrentUser, db: DB, org_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None, limit: int = 100) -> list[FileOut]:
    scope = resolve_scope(db, user, org_id, project_id)
    q = select(ProjectFile).where(ProjectFile.org_id == scope.org_id, ProjectFile.kind == "upload")
    q = q.where(ProjectFile.project_id == scope.project_id) if scope.project_id else q.where(ProjectFile.project_id.is_(None))
    return [file_out(f) for f in db.scalars(q.order_by(ProjectFile.created_at.desc()).limit(min(limit, 500)))]


def _get_file(db: DB, user: CurrentUser, file_id: uuid.UUID, min_role: str = "viewer") -> ProjectFile:
    f = db.get(ProjectFile, file_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if f.project_id:
        require_project(db, user, f.project_id, min_role)
    else:
        require_org(db, user, f.org_id, min_role)
    return f


@router.get("/{file_id}", response_model=FileOut)
def get_file(file_id: uuid.UUID, user: CurrentUser, db: DB) -> FileOut:
    return file_out(_get_file(db, user, file_id))


@router.get("/{file_id}/text")
def get_text(file_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, object]:
    f = _get_file(db, user, file_id)
    return {"text": f.extracted_text, "error": f.extraction_error, "chars": len(f.extracted_text or "")}


@router.get("/{file_id}/download")
def download(file_id: uuid.UUID, user: CurrentUser, db: DB, inline: bool = False) -> Response:
    f = _get_file(db, user, file_id)
    return safe_file_response(get_storage().get_bytes(f.storage_key), f.path, f.mime_type, inline)


def safe_file_response(data: bytes, name: str, mime: str, inline: bool = False) -> Response:
    # Active content (HTML/SVG/JS) is never rendered inline from the API origin.
    active = mime in ("text/html", "image/svg+xml", "application/javascript", "text/javascript", "application/xhtml+xml")
    disp = "inline" if inline and not active else "attachment"
    quoted = re.sub(r'["\\\r\n]', "_", name)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'{disp}; filename="{quoted}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> None:
    f = _get_file(db, user, file_id, "editor")
    get_storage().delete(f.storage_key)
    db.delete(f)
    audit(db, "file.delete", user_id=user.id, org_id=f.org_id, target_type="file", target_id=f.id, ip=client_ip(request), name=f.path)
