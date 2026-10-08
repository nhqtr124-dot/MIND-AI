from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from ..config import get_settings
from ..db import utcnow
from ..deps import DB, CurrentUser, client_ip, require_org
from ..models import ROLE_RANK, Invitation, Membership, Organization
from ..schemas import InviteIn, InviteOut, MemberOut, OrgCreate, OrgOut, OrgUpdate, OrgWithRole, RoleChange
from ..security import new_opaque_token
from ..services.audit import audit
from .auth import unique_slug

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.get("", response_model=list[OrgWithRole])
def list_orgs(user: CurrentUser, db: DB) -> list[OrgWithRole]:
    rows = db.execute(select(Organization, Membership.role).join(Membership, Membership.org_id == Organization.id).where(Membership.user_id == user.id).order_by(Organization.is_personal.desc(), Organization.name))
    return [OrgWithRole.model_validate({**OrgOut.model_validate(o).model_dump(), "role": r}) for o, r in rows]


@router.post("", response_model=OrgWithRole, status_code=201)
def create_org(body: OrgCreate, request: Request, user: CurrentUser, db: DB) -> OrgWithRole:
    org = Organization(name=body.name.strip(), slug=unique_slug(db, body.name))
    db.add(org)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    audit(db, "org.create", user_id=user.id, org_id=org.id, ip=client_ip(request))
    return OrgWithRole.model_validate({**OrgOut.model_validate(org).model_dump(), "role": "owner"})


@router.get("/{org_id}", response_model=OrgWithRole)
def get_org(org_id: uuid.UUID, user: CurrentUser, db: DB) -> OrgWithRole:
    org, role = require_org(db, user, org_id)
    return OrgWithRole.model_validate({**OrgOut.model_validate(org).model_dump(), "role": role})


@router.patch("/{org_id}", response_model=OrgOut)
def update_org(org_id: uuid.UUID, body: OrgUpdate, request: Request, user: CurrentUser, db: DB) -> Organization:
    org, _ = require_org(db, user, org_id, "admin")
    if body.name is not None:
        org.name = body.name.strip()
    if body.clear_budgets:
        org.monthly_budget_usd = org.user_daily_budget_usd = None
    if body.monthly_budget_usd is not None:
        org.monthly_budget_usd = body.monthly_budget_usd
    if body.user_daily_budget_usd is not None:
        org.user_daily_budget_usd = body.user_daily_budget_usd
    if body.fallback_policy is not None:
        org.fallback_policy = body.fallback_policy
    audit(db, "org.update", user_id=user.id, org_id=org.id, ip=client_ip(request), changes=body.model_dump(exclude_none=True, mode="json"))
    return org


@router.get("/{org_id}/members", response_model=list[MemberOut])
def members(org_id: uuid.UUID, user: CurrentUser, db: DB) -> list[MemberOut]:
    require_org(db, user, org_id)
    rows = db.scalars(select(Membership).where(Membership.org_id == org_id).order_by(Membership.created_at)).unique()
    return [MemberOut(user_id=m.user_id, email=m.user.email, display_name=m.user.display_name, role=m.role, joined_at=m.created_at) for m in rows]  # type: ignore[arg-type]


def _owner_count(db: DB, org_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == "owner")) or 0


@router.patch("/{org_id}/members/{member_id}", response_model=MemberOut)
def change_role(org_id: uuid.UUID, member_id: uuid.UUID, body: RoleChange, request: Request, user: CurrentUser, db: DB) -> MemberOut:
    _, my_role = require_org(db, user, org_id, "admin")
    m = db.scalar(select(Membership).where(Membership.org_id == org_id, Membership.user_id == member_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    # Admins cannot grant or change owner status; only owners can.
    if (body.role == "owner" or m.role == "owner") and my_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners can grant or change the owner role")
    if m.role == "owner" and body.role != "owner" and _owner_count(db, org_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "An organization must keep at least one owner")
    old, m.role = m.role, body.role
    audit(db, "org.member_role_changed", user_id=user.id, org_id=org_id, target_type="user", target_id=member_id, ip=client_ip(request), old=old, new=body.role)
    return MemberOut(user_id=m.user_id, email=m.user.email, display_name=m.user.display_name, role=m.role, joined_at=m.created_at)  # type: ignore[arg-type]


@router.delete("/{org_id}/members/{member_id}", status_code=204)
def remove_member(org_id: uuid.UUID, member_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> None:
    org, my_role = require_org(db, user, org_id, "viewer" if member_id == user.id else "admin")
    m = db.scalar(select(Membership).where(Membership.org_id == org_id, Membership.user_id == member_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    if m.role == "owner" and member_id != user.id and my_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners can remove an owner")
    if m.role == "owner" and _owner_count(db, org_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "An organization must keep at least one owner")
    if org.is_personal and member_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot leave your personal workspace")
    db.delete(m)
    audit(db, "org.member_removed", user_id=user.id, org_id=org_id, target_type="user", target_id=member_id, ip=client_ip(request))


@router.get("/{org_id}/invitations", response_model=list[InviteOut])
def list_invitations(org_id: uuid.UUID, user: CurrentUser, db: DB) -> list[InviteOut]:
    require_org(db, user, org_id, "admin")
    rows = db.scalars(select(Invitation).where(Invitation.org_id == org_id).order_by(Invitation.created_at.desc()))
    return [InviteOut(id=i.id, email=i.email, role=i.role, expires_at=i.expires_at, accepted_at=i.accepted_at, revoked_at=i.revoked_at, created_at=i.created_at) for i in rows]


@router.post("/{org_id}/invitations", response_model=InviteOut, status_code=201)
def invite(org_id: uuid.UUID, body: InviteIn, request: Request, user: CurrentUser, db: DB) -> InviteOut:
    _, my_role = require_org(db, user, org_id, "admin")
    if ROLE_RANK[body.role] > ROLE_RANK[my_role]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot invite someone with a higher role than yours")
    token, token_hash = new_opaque_token()
    inv = Invitation(org_id=org_id, email=body.email.lower(), role=body.role, token_hash=token_hash, invited_by=user.id, expires_at=utcnow() + timedelta(days=7))
    db.add(inv)
    db.flush()
    audit(db, "org.invite", user_id=user.id, org_id=org_id, target_type="invitation", target_id=inv.id, ip=client_ip(request), email=inv.email, role=inv.role)
    # No email service is configured by default: the inviter shares the link themselves.
    accept_url = f"{get_settings().cors_origins[0]}/invite?token={token}"
    return InviteOut(id=inv.id, email=inv.email, role=inv.role, expires_at=inv.expires_at, accepted_at=None, revoked_at=None, created_at=inv.created_at, token=token, accept_url=accept_url)


@router.delete("/{org_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(org_id: uuid.UUID, invitation_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    require_org(db, user, org_id, "admin")
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    inv.revoked_at = utcnow()
    audit(db, "org.invitation_revoked", user_id=user.id, org_id=org_id, target_type="invitation", target_id=inv.id)
