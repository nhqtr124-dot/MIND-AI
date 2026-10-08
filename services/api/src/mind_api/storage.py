"""Object storage: local filesystem for development, S3-compatible for production."""

from __future__ import annotations

import hashlib
import shutil
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import get_settings


class StorageError(Exception):
    pass


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def download_to(self, key: str, path: Path) -> Path: ...


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise StorageError(f"invalid storage key: {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError("key escapes storage root")
        return p

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, p)

    def get_bytes(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"object not found: {key}") from exc

    def open(self, key: str) -> BinaryIO:
        try:
            return self._path(key).open("rb")
        except FileNotFoundError as exc:
            raise StorageError(f"object not found: {key}") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def download_to(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), path)
        return path


class S3Storage:
    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise StorageError("boto3 is required for S3 storage (pip install mind-api[s3])") from exc
        s = get_settings()
        if not s.s3_bucket:
            raise StorageError("MIND_S3_BUCKET is required for S3 storage")
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
        )

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=content_type)

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream") -> None:
        self.client.upload_file(
            str(path), self.bucket, _check_key(key), ExtraArgs={"ContentType": content_type}
        )

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()  # type: ignore[no-any-return]
        except self.client.exceptions.NoSuchKey as exc:
            raise StorageError(f"object not found: {key}") from exc

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"]  # type: ignore[no-any-return]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except Exception:  # noqa: BLE001 - botocore ClientError on 404
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def download_to(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, _check_key(key), str(path))
        return path


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    return S3Storage() if s.storage_backend == "s3" else LocalStorage(s.storage_dir)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
