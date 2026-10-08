from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from mind_ai import ProviderError
from sqlalchemy import select

from ..deps import DB, CurrentUser, client_ip, require_org
from ..models import ModelConfig, ModelProvider
from ..schemas import ModelConfigOut, ModelConfigUpdate, ProviderCreate, ProviderCreated, ProviderKindOut, ProviderOut, ProviderUpdate
from ..security import encrypt_secret, secret_hint
from ..services.audit import audit
from ..services.providers import PROVIDER_KINDS, apply_discovery, discover, mark_error, org_models

router = APIRouter(tags=["ai-providers"])


def model_out(m: ModelConfig) -> ModelConfigOut:
    out = ModelConfigOut.model_validate(m)
    out.provider_kind, out.provider_name, out.provider_status = m.provider.kind, m.provider.name, m.provider.status
    return out


@router.get("/provider-kinds", response_model=list[ProviderKindOut])
def provider_kinds() -> list[ProviderKindOut]:
    return [ProviderKindOut(kind=k, **v) for k, v in PROVIDER_KINDS.items()]


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(org_id: uuid.UUID, user: CurrentUser, db: DB) -> list[ModelProvider]:
    require_org(db, user, org_id)
    return list(db.scalars(select(ModelProvider).where(ModelProvider.org_id == org_id).order_by(ModelProvider.created_at)))


@router.post("/providers", response_model=ProviderCreated, status_code=201)
async def create_provider(body: ProviderCreate, request: Request, user: CurrentUser, db: DB) -> ProviderCreated:
    require_org(db, user, body.org_id, "admin")
    if PROVIDER_KINDS[body.kind]["needs_key"] and not body.api_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{PROVIDER_KINDS[body.kind]['label']} requires an API key")
    if db.scalar(select(ModelProvider.id).where(ModelProvider.org_id == body.org_id, ModelProvider.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A provider with this name already exists")
    p = ModelProvider(
        org_id=body.org_id, kind=body.kind, name=body.name, base_url=body.base_url or None,
        api_key_encrypted=encrypt_secret(body.api_key) if body.api_key else None,
        api_key_hint=secret_hint(body.api_key) if body.api_key else None, created_by=user.id,
    )  # fmt: skip
    db.add(p)
    db.flush()
    audit(db, "provider.create", user_id=user.id, org_id=body.org_id, target_type="provider", target_id=p.id, ip=client_ip(request), kind=body.kind)
    return await _verify(db, p)


async def _verify(db: DB, p: ModelProvider) -> ProviderCreated:
    """Validate credentials by listing models; never reports success without a real provider response."""
    try:
        found = await discover(p)
    except ProviderError as exc:
        mark_error(p, exc)
        return ProviderCreated(provider=ProviderOut.model_validate(p), discovered=0, added=0, error=exc.to_dict())
    added, total = apply_discovery(db, p, found)
    return ProviderCreated(provider=ProviderOut.model_validate(p), discovered=total, added=added)


@router.post("/providers/{provider_id}/verify", response_model=ProviderCreated)
async def verify_provider(provider_id: uuid.UUID, user: CurrentUser, db: DB) -> ProviderCreated:
    p = db.get(ModelProvider, provider_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    require_org(db, user, p.org_id, "admin")
    return await _verify(db, p)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: uuid.UUID, body: ProviderUpdate, user: CurrentUser, db: DB) -> ModelProvider:
    p = db.get(ModelProvider, provider_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    require_org(db, user, p.org_id, "admin")
    if body.name:
        p.name = body.name
    if body.base_url is not None:
        p.base_url = body.base_url or None
    if body.api_key:
        p.api_key_encrypted, p.api_key_hint, p.status = encrypt_secret(body.api_key), secret_hint(body.api_key), "unverified"
    audit(db, "provider.update", user_id=user.id, org_id=p.org_id, target_type="provider", target_id=p.id, key_changed=bool(body.api_key))
    return p


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: uuid.UUID, request: Request, user: CurrentUser, db: DB) -> None:
    p = db.get(ModelProvider, provider_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    require_org(db, user, p.org_id, "admin")
    db.delete(p)
    audit(db, "provider.delete", user_id=user.id, org_id=p.org_id, target_type="provider", target_id=provider_id, ip=client_ip(request))


@router.get("/models", response_model=list[ModelConfigOut])
def list_models(org_id: uuid.UUID, user: CurrentUser, db: DB, enabled_only: bool = False, capability: str | None = None) -> list[ModelConfigOut]:
    require_org(db, user, org_id)
    return [model_out(m) for m in org_models(db, org_id, capability, enabled_only)]


@router.patch("/models/{model_id}", response_model=ModelConfigOut)
def update_model(model_id: uuid.UUID, body: ModelConfigUpdate, user: CurrentUser, db: DB) -> ModelConfigOut:
    m = db.get(ModelConfig, model_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    require_org(db, user, m.org_id, "admin")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    audit(db, "model.update", user_id=user.id, org_id=m.org_id, target_type="model", target_id=m.id, changes=body.model_dump(exclude_unset=True, mode="json"))
    return model_out(m)
