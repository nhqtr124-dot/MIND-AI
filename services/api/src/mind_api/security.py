from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_ph = PasswordHasher()
ALGO = "HS256"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


# Constant-time dummy verification so unknown emails take as long as wrong passwords.
_DUMMY_HASH = _ph.hash("not-a-real-password")


def dummy_verify() -> None:
    verify_password("x", _DUMMY_HASH)


def create_access_token(user_id: uuid.UUID) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
        "typ": "access",
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_access_token(token: str) -> uuid.UUID | None:
    try:
        data = jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "access":
        return None
    try:
        return uuid.UUID(data["sub"])
    except (KeyError, ValueError):
        return None


def new_opaque_token() -> tuple[str, str]:
    """Return (token, sha256 hash). Only the hash is stored."""
    tok = secrets.token_urlsafe(32)
    return tok, sha256(tok)


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


# ---------------------------------------------------------------------------- credential encryption


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    if s.encryption_key:
        return Fernet(s.encryption_key.encode())
    # Dev/test only (prod requires MIND_ENCRYPTION_KEY): derive a stable key from the secret.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("fernet:" + s.secret_key).encode()).digest()))


def encrypt_secret(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode())


def decrypt_secret(token: bytes | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token).decode()
    except InvalidToken:
        return None


def secret_hint(plain: str) -> str:
    return f"…{plain[-4:]}" if len(plain) >= 8 else "…"


# ---------------------------------------------------------------------------- signed tokens (downloads, previews)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_payload(purpose: str, payload: dict[str, Any], ttl_s: int) -> str:
    body = dict(payload, p=purpose, exp=int(time.time()) + ttl_s)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(get_settings().secret_key.encode(), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(mac)}"


def verify_payload(purpose: str, token: str) -> dict[str, Any] | None:
    try:
        raw_s, mac_s = token.split(".", 1)
        raw, mac = _unb64(raw_s), _unb64(mac_s)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(get_settings().secret_key.encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        body: dict[str, Any] = json.loads(raw)
    except ValueError:
        return None
    if body.get("p") != purpose or int(body.get("exp", 0)) < time.time():
        return None
    return body
