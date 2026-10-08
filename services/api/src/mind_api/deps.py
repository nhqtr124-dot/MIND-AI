"""Request dependencies: DB session, authentication, CSRF, tenant authorization, rate limits.

Authorization is enforced here, on the backend, for every route. Resources in
an organization the caller does not belong to are reported as 404, so object
existence does not leak across tenants.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .models import ROLE_RANK, Membership, Organization, Project, ProjectMember, User
from .security import decode_access_token

ACCESS_COOKIE = "mind_access"
REFRESH_COOKIE = "mind_refresh"
CSRF_COOKIE = "mind_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


DB = Annotated[Session, Depends(get_db)]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def current_user(request: Request, db: DB) -> User:
    token: str | None = None
    auth = request.headers.get("authorization", "")
    via_cookie = False
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif request.cookies.get(ACCESS_COOKIE):
        token = request.cookies[ACCESS_COOKIE]
        via_cookie = True
    if not token:
        raise _unauthorized()
    if via_cookie and request.method not in SAFE_METHODS:
        # Double-submit CSRF check for cookie-authenticated, state-changing requests.
        header = request.headers.get(CSRF_HEADER, "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not header or not cookie or not hmac.compare_digest(header, cookie):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    uid = decode_access_token(token)
    if uid is None:
        raise _unauthorized("Invalid or expired token")
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive")
    request.state.user_id = user.id
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def not_found(what: str = "Resource") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def forbidden(detail: str = "You do not have permission to do this") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)


def org_role(db: Session, user: User, org_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(Membership.role).where(Membership.org_id == org_id, Membership.user_id == user.id)
    )


def require_org(
    db: Session, user: User, org_id: uuid.UUID, min_role: str = "viewer"
) -> tuple[Organization, str]:
    role = org_role(db, user, org_id)
    org = db.get(Organization, org_id) if role else None
    if org is None or role is None:
        raise not_found("Organization")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise forbidden(f"Requires {min_role} role in this organization")
    return org, role


def project_role(db: Session, user: User, project: Project) -> str | None:
    role = org_role(db, user, project.org_id)
    if role is None:
        return None
    if project.visibility == "org" or role in ("owner", "admin"):
        return role
    pm = db.get(ProjectMember, (project.id, user.id))
    return pm.role if pm else None


def require_project(
    db: Session, user: User, project_id: uuid.UUID, min_role: str = "viewer"
) -> tuple[Project, str]:
    project = db.get(Project, project_id)
    role = project_role(db, user, project) if project else None
    if project is None or role is None or project.archived_at is not None:
        raise not_found("Project")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise forbidden(f"Requires {min_role} role on this project")
    return project, role


@dataclass
class Scope:
    """Resolved (org, optional project) the caller is acting in."""

    org_id: uuid.UUID
    project_id: uuid.UUID | None
    role: str


def resolve_scope(
    db: Session, user: User, org_id: uuid.UUID | None, project_id: uuid.UUID | None, min_role: str = "viewer"
) -> Scope:
    if project_id is not None:
        project, role = require_project(db, user, project_id, min_role)
        if org_id is not None and org_id != project.org_id:
            raise not_found("Project")
        return Scope(project.org_id, project.id, role)
    if org_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "org_id or project_id is required")
    _, role = require_org(db, user, org_id, min_role)
    return Scope(org_id, None, role)


# ---------------------------------------------------------------------------- rate limiting


class RateLimiter:
    """Fixed-window limiter backed by Redis when configured, else process memory."""

    def __init__(self, redis_url: str | None) -> None:
        self._mem: dict[str, deque[float]] = defaultdict(deque)
        self._redis = None
        if redis_url:
            try:
                import redis

                client = redis.Redis.from_url(redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
                client.ping()
                self._redis = client
            except Exception:  # noqa: BLE001 - fall back to in-memory limiting
                self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def hit(self, key: str, limit: int, window_s: int = 60) -> bool:
        if self._redis is not None:
            try:
                bucket = f"mind:rl:{key}:{int(time.time() // window_s)}"
                pipe = self._redis.pipeline()
                pipe.incr(bucket)
                pipe.expire(bucket, window_s + 1)
                count = int(pipe.execute()[0])
                return count <= limit
            except Exception:  # noqa: BLE001 - Redis outage: degrade to the in-memory window
                logging.getLogger("mind.ratelimit").warning(
                    "redis rate limiter unavailable; using in-memory fallback"
                )
        now = time.monotonic()
        q = self._mem[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


@lru_cache
def get_rate_limiter() -> RateLimiter:
    s = get_settings()
    return RateLimiter(s.redis_url if s.env != "test" else None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, per_minute: int | None = None):  # type: ignore[no-untyped-def]
    def dep(request: Request) -> None:
        limit = per_minute or get_settings().rate_limit_per_minute
        who = getattr(request.state, "user_id", None) or client_ip(request)
        if not get_rate_limiter().hit(f"{scope}:{who}", limit):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded; try again shortly")

    return Depends(dep)
