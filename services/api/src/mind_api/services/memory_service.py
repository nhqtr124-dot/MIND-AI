"""Layered memory with workspace isolation.

* user scope: visible only to its owner
* project scope: visible to people who can access the project
* team scope: visible to all members of the organization

Retrieval uses pgvector cosine distance when an embedding model is configured
and the entry has been embedded; otherwise PostgreSQL full-text search.
Deletion is a hard delete of the entry and its embeddings (see docs/SECURITY.md
for backup retention).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from mind_ai import ProviderError
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..deps import project_role
from ..jobs import JobContext, JobFailed, RetryableJobError, handler
from ..models import MemoryEmbedding, MemoryEntry, ModelConfig, Project, User
from .audit import record_usage
from .providers import adapter_for, org_models


def accessible_filter(db: Session, user: User, org_id: uuid.UUID) -> Any:
    project_ids = [
        p.id
        for p in db.scalars(select(Project).where(Project.org_id == org_id, Project.archived_at.is_(None)))
        if project_role(db, user, p)
    ]
    return and_(
        MemoryEntry.org_id == org_id,
        or_(
            and_(MemoryEntry.scope == "user", MemoryEntry.user_id == user.id),
            MemoryEntry.scope == "team",
            and_(
                MemoryEntry.scope == "project", MemoryEntry.project_id.in_(project_ids or [uuid.UUID(int=0)])
            ),
        ),
    )


def embedding_model(db: Session, org_id: uuid.UUID) -> ModelConfig | None:
    models = org_models(db, org_id, "embeddings")
    return models[0] if models else None


def search(
    db: Session,
    user: User,
    org_id: uuid.UUID,
    query: str,
    *,
    project_id: uuid.UUID | None = None,
    k: int = 5,
    query_vector: list[float] | None = None,
    embedding_model_id: uuid.UUID | None = None,
) -> list[tuple[MemoryEntry, float, str]]:
    """Hybrid retrieval: vector and full-text rankings fused with reciprocal rank fusion (k=60)."""
    base = accessible_filter(db, user, org_id)
    if project_id is not None:
        base = and_(base, or_(MemoryEntry.scope != "project", MemoryEntry.project_id == project_id))
    pool = max(k * 3, 10)
    rankings: dict[str, list[MemoryEntry]] = {}
    if query_vector is not None and embedding_model_id is not None:
        dist = MemoryEmbedding.embedding.cosine_distance(query_vector)
        q: Select[Any] = (
            select(MemoryEntry)
            .join(MemoryEmbedding, MemoryEmbedding.memory_id == MemoryEntry.id)
            .where(
                base,
                MemoryEmbedding.model_config_id == embedding_model_id,
                MemoryEmbedding.dim == len(query_vector),
            )
            .order_by(dist)
            .limit(pool)
        )
        rankings["vector"] = list(db.scalars(q))
    if query.strip():
        tsq = func.plainto_tsquery("simple", query)
        tsv = func.to_tsvector("simple", MemoryEntry.content)
        q = (
            select(MemoryEntry)
            .where(base, tsv.op("@@")(tsq))
            .order_by(func.ts_rank(tsv, tsq).desc())
            .limit(pool)
        )
        rankings["fulltext"] = list(db.scalars(q))
        if not rankings["fulltext"]:
            # plainto_tsquery ANDs terms; fall back to matching any significant word.
            words = [w for w in query.split() if len(w) > 2][:6]
            if words:
                q = (
                    select(MemoryEntry)
                    .where(base, or_(*[MemoryEntry.content.ilike(f"%{w}%") for w in words]))
                    .limit(pool)
                )
                rankings["substring"] = list(db.scalars(q))
    scores: dict[uuid.UUID, float] = {}
    methods: dict[uuid.UUID, list[str]] = {}
    entries: dict[uuid.UUID, MemoryEntry] = {}
    for method, ranked in rankings.items():
        for rank, e in enumerate(ranked):
            scores[e.id] = scores.get(e.id, 0.0) + 1.0 / (60 + rank + 1)
            methods.setdefault(e.id, []).append(method)
            entries[e.id] = e
    ordered = sorted(scores, key=lambda i: (-scores[i], str(i)))
    return [(entries[i], round(scores[i], 5), "+".join(methods[i])) for i in ordered[:k]]


async def embed_query(
    db_factory: Any, org_id: uuid.UUID, text: str
) -> tuple[list[float] | None, uuid.UUID | None]:
    """Embed a query if the org has an embedding model; failures degrade to full-text search."""

    def _model() -> tuple[Any, uuid.UUID | None, str | None]:
        with db_factory() as db:
            m = embedding_model(db, org_id)
            return (m.provider, m.id, m.model_name) if m else (None, None, None)

    provider, mid, name = await asyncio.to_thread(_model)
    if provider is None or name is None:
        return None, None
    adapter = adapter_for(provider)
    try:
        res = await adapter.embed(name, [text])
        return res.vectors[0], mid
    except ProviderError:
        return None, None
    finally:
        await adapter.aclose()


@handler("memory.embed")
def memory_embed(ctx: JobContext) -> dict[str, Any]:
    mid = uuid.UUID(ctx.payload["memory_id"])
    with session_scope() as db:
        entry = db.get(MemoryEntry, mid)
        if entry is None:
            return {"skipped": "memory deleted"}
        model = embedding_model(db, entry.org_id)
        if model is None:
            return {"skipped": "no embedding model configured"}
        provider, model_name, model_id, content = model.provider, model.model_name, model.id, entry.content
        db.expunge(provider)

    async def go() -> Any:
        adapter = adapter_for(provider)
        try:
            return await adapter.embed(model_name, [content])
        finally:
            await adapter.aclose()

    try:
        res = asyncio.run(go())
    except ProviderError as exc:
        if exc.retryable:
            raise RetryableJobError(exc.message) from exc
        raise JobFailed(exc.message) from exc
    vec = res.vectors[0]
    with session_scope() as db:
        if db.get(MemoryEntry, mid) is None:
            return {"skipped": "memory deleted during embedding"}
        existing = db.scalar(
            select(MemoryEmbedding).where(
                MemoryEmbedding.memory_id == mid, MemoryEmbedding.model_config_id == model_id
            )
        )
        if existing:
            existing.embedding, existing.dim = vec, len(vec)
        else:
            db.add(MemoryEmbedding(memory_id=mid, model_config_id=model_id, dim=len(vec), embedding=vec))
        record_usage(
            db, org_id=ctx.org_id, user_id=ctx.user_id, category="embedding", provider_kind=provider.kind, model_name=model_name,
            input_tokens=res.usage.input_tokens, cost_usd=None, ref_type="memory", ref_id=mid,
        )  # fmt: skip
    return {"memory_id": str(mid), "dim": len(vec)}


def memory_dict(m: MemoryEntry) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "scope": m.scope,
        "project_id": str(m.project_id) if m.project_id else None,
        "content": m.content,
        "source": m.source,
        "tags": m.tags,
        "user_id": str(m.user_id),
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
    }
