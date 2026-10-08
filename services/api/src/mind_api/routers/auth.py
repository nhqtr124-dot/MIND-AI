from __future__ import annotations

import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..deps import ACCESS_COOKIE, CSRF_COOKIE, DB, REFRESH_COOKIE, CurrentUser, client_ip, rate_limit
from ..models import Invitation, Membership, Organization, RefreshToken, User
from ..schemas import (
    AcceptInviteIn,
    LoginIn,
    PasswordChange,
    RefreshIn,
    RegisterIn,
    TokenOut,
    UserOut,
    UserUpdate,
)
from ..security import (
    create_access_token,
    dummy_verify,
    hash_password,
    new_opaque_token,
    sha256,
    verify_password,
)
from ..services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
AUTH_LIMIT = rate_limit("auth", get_settings().auth_rate_limit_per_minute)


def unique_slug(db: Session, base: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60] or "org"
    candidate = slug
    while db.scalar(select(Organization.id).where(Organization.slug == candidate)):
        candidate = f"{slug}-{secrets.token_hex(3)}"
    return candidate


def _issue(db: Session, user: User, response: Response, request: Request) -> TokenOut:
    s = get_settings()
    access = create_access_token(user.id)
    refresh, refresh_hash = new_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=utcnow() + timedelta(days=s.refresh_token_days),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    csrf = secrets.token_urlsafe(24)
    kw = {"secure": s.cookie_secure, "samesite": "lax"}
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, max_age=s.access_token_minutes * 60, path="/", **kw
    )  # type: ignore[arg-type]
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        httponly=True,
        max_age=s.refresh_token_days * 86400,
        path="/api/v1/auth",
        **kw,
    )  # type: ignore[arg-type]
    response.set_cookie(
        CSRF_COOKIE, csrf, httponly=False, max_age=s.refresh_token_days * 86400, path="/", **kw
    )  # type: ignore[arg-type]
    return TokenOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=s.access_token_minutes * 60,
        csrf_token=csrf,
        user=UserOut.model_validate(user),
    )


def _accept_invitation(db: Session, user: User, token: str) -> Invitation:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == sha256(token)))
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation is invalid, expired or already used")
    if inv.email.lower() != user.email.lower():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This invitation was sent to a different email address"
        )
    existing = db.scalar(
        select(Membership).where(Membership.org_id == inv.org_id, Membership.user_id == user.id)
    )
    if existing is None:
        db.add(Membership(org_id=inv.org_id, user_id=user.id, role=inv.role))
    inv.accepted_at = utcnow()
    return inv


@router.post("/register", response_model=TokenOut, status_code=201, dependencies=[AUTH_LIMIT])
def register(body: RegisterIn, request: Request, response: Response, db: DB) -> TokenOut:
    s = get_settings()
    if not s.allow_registration and not body.invitation_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is invite-only on this server")
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
        locale=body.locale,
    )
    db.add(user)
    db.flush()
    org = Organization(
        name=f"{user.display_name}'s workspace", slug=unique_slug(db, user.display_name), is_personal=True
    )
    db.add(org)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    if body.invitation_token:
        _accept_invitation(db, user, body.invitation_token)
    audit(db, "user.register", user_id=user.id, org_id=org.id, ip=client_ip(request))
    user.last_login_at = utcnow()
    return _issue(db, user, response, request)


@router.post("/login", response_model=TokenOut, dependencies=[AUTH_LIMIT])
def login(body: LoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None:
        dummy_verify()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not verify_password(body.password, user.password_hash) or not user.is_active:
        audit(db, "user.login_failed", user_id=user.id, ip=client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    user.last_login_at = utcnow()
    audit(db, "user.login", user_id=user.id, ip=client_ip(request))
    return _issue(db, user, response, request)


@router.post("/refresh", response_model=TokenOut, dependencies=[AUTH_LIMIT])
def refresh(request: Request, response: Response, db: DB, body: RefreshIn | None = None) -> TokenOut:
    token = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256(token)))
    if rt is None or rt.expires_at < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token invalid or expired")
    if rt.revoked_at is not None:
        # Reuse of a rotated token suggests theft: revoke every session of this user.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == rt.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        audit(db, "auth.refresh_reuse_detected", user_id=rt.user_id, ip=client_ip(request))
        db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Refresh token reuse detected; all sessions were signed out"
        )
    user = db.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive")
    rt.revoked_at = utcnow()
    return _issue(db, user, response, request)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DB, body: RefreshIn | None = None) -> None:
    token = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if token:
        rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256(token)))
        if rt and rt.revoked_at is None:
            rt.revoked_at = utcnow()
    for c, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/v1/auth"), (CSRF_COOKIE, "/")):
        response.delete_cookie(c, path=path)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: UserUpdate, user: CurrentUser, db: DB) -> User:
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.locale is not None:
        user.locale = body.locale
    return user


@router.post("/password", status_code=204, dependencies=[AUTH_LIMIT])
def change_password(body: PasswordChange, request: Request, user: CurrentUser, db: DB) -> None:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    audit(db, "user.password_changed", user_id=user.id, ip=client_ip(request))


@router.post("/invitations/accept", status_code=200)
def accept_invitation(body: AcceptInviteIn, user: CurrentUser, db: DB) -> dict[str, str]:
    inv = _accept_invitation(db, user, body.token)
    audit(
        db,
        "org.invitation_accepted",
        user_id=user.id,
        org_id=inv.org_id,
        target_type="invitation",
        target_id=inv.id,
    )
    return {"org_id": str(inv.org_id), "role": inv.role}
