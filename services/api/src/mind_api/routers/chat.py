from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, select

from ..db import session_scope
from ..deps import DB, CurrentUser, rate_limit, require_org, require_project
from ..models import Conversation, Message, ModelConfig, ProjectFile
from ..schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    RegenerateIn,
    SearchHitOut,
    SendMessageIn,
    TurnResult,
)
from ..services.chat_service import TurnInput, _path_to, conversation_dict, export_markdown, message_dict, run_turn

router = APIRouter(prefix="/conversations", tags=["chat"])
CHAT_LIMIT = rate_limit("chat", 30)


def get_conv(db: DB, user: CurrentUser, conv_id: uuid.UUID) -> Conversation:
    c = db.get(Conversation, conv_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    require_org(db, user, c.org_id)
    return c


def _check_model(db: DB, org_id: uuid.UUID, model_id: uuid.UUID | None) -> None:
    if model_id is None:
        return
    m = db.get(ModelConfig, model_id)
    if m is None or m.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    if not m.enabled or "chat" not in m.capabilities:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That model is not enabled for chat")


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    org_id: uuid.UUID, user: CurrentUser, db: DB, project_id: uuid.UUID | None = None, folder: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    require_org(db, user, org_id)
    q = select(Conversation).where(Conversation.org_id == org_id, Conversation.user_id == user.id)
    if project_id:
        q = q.where(Conversation.project_id == project_id)
    if folder is not None:
        q = q.where(Conversation.folder == (folder or None))
    return [conversation_dict(c) for c in db.scalars(q.order_by(Conversation.updated_at.desc()).limit(min(limit, 500)))]


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, user: CurrentUser, db: DB) -> dict[str, Any]:
    require_org(db, user, body.org_id)
    if body.project_id:
        p, _ = require_project(db, user, body.project_id)
        if p.org_id != body.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if body.model_mode == "manual" and body.model_config_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Manual mode requires model_config_id")
    _check_model(db, body.org_id, body.model_config_id)
    c = Conversation(
        org_id=body.org_id, project_id=body.project_id, user_id=user.id, title=body.title or "New chat", folder=body.folder,
        model_mode=body.model_mode, model_config_id=body.model_config_id, preference=body.preference,
        system_prompt=body.system_prompt, use_memory=body.use_memory,
    )  # fmt: skip
    db.add(c)
    db.flush()
    return conversation_dict(c)


@router.get("/search", response_model=list[SearchHitOut])
def search(org_id: uuid.UUID, q: str, user: CurrentUser, db: DB, limit: int = 30) -> list[SearchHitOut]:
    require_org(db, user, org_id)
    if not q.strip():
        return []
    tsq = func.plainto_tsquery("simple", q)
    snippet = func.ts_headline("simple", Message.content, tsq, "MaxWords=30, MinWords=10, StartSel=<<, StopSel=>>")
    rows = db.execute(
        select(Message.id, Message.role, Message.created_at, Conversation.id, Conversation.title, snippet)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.org_id == org_id, Conversation.user_id == user.id, func.to_tsvector("simple", Message.content).op("@@")(tsq))
        .order_by(Message.created_at.desc())
        .limit(min(limit, 100))
    ).all()
    hits = [SearchHitOut(message_id=r[0], role=r[1], created_at=r[2].isoformat(), conversation_id=r[3], conversation_title=r[4], snippet=r[5]) for r in rows]
    title_rows = db.execute(
        select(Conversation.id, Conversation.title, Conversation.updated_at)
        .where(Conversation.org_id == org_id, Conversation.user_id == user.id, Conversation.title.ilike(f"%{q.strip()[:100]}%"))
        .limit(10)
    ).all()
    seen = {h.conversation_id for h in hits}
    for cid, title, upd in title_rows:
        if cid not in seen:
            hits.insert(0, SearchHitOut(conversation_id=cid, conversation_title=title, message_id=cid, role="title", snippet=title, created_at=upd.isoformat()))
    return hits


@router.get("/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: uuid.UUID, user: CurrentUser, db: DB) -> dict[str, Any]:
    c = get_conv(db, user, conv_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at)).all()
    return {**conversation_dict(c), "messages": [message_dict(m) for m in msgs]}


@router.patch("/{conv_id}", response_model=ConversationOut)
def update_conversation(conv_id: uuid.UUID, body: ConversationUpdate, user: CurrentUser, db: DB) -> dict[str, Any]:
    c = get_conv(db, user, conv_id)
    data = body.model_dump(exclude_unset=True)
    if "model_config_id" in data:
        _check_model(db, c.org_id, data["model_config_id"])
    if data.get("model_mode") == "manual" and not (data.get("model_config_id") or c.model_config_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Manual mode requires model_config_id")
    if data.get("project_id"):
        p, _ = require_project(db, user, data["project_id"])
        if p.org_id != c.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if data.get("current_leaf_id"):
        leaf = db.get(Message, data["current_leaf_id"])
        if leaf is None or leaf.conversation_id != c.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    for k, v in data.items():
        setattr(c, k, v)
    return conversation_dict(c)


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(conv_id: uuid.UUID, user: CurrentUser, db: DB) -> None:
    c = get_conv(db, user, conv_id)
    db.delete(c)


def _validate_attachments(db: DB, user: CurrentUser, c: Conversation, ids: list[uuid.UUID]) -> None:
    for fid in ids:
        f = db.get(ProjectFile, fid)
        if f is None or f.org_id != c.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Attachment {fid} not found")
        if f.project_id:
            require_project(db, user, f.project_id)


def _sse(events: AsyncIterator[dict[str, Any]], request: Request) -> StreamingResponse:
    async def gen() -> AsyncIterator[bytes]:
        async for ev in events:
            yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _collect(events: AsyncIterator[dict[str, Any]]) -> TurnResult:
    out = [ev async for ev in events]
    aid = next((e["assistant_message_id"] for e in out if e["type"] == "start"), None)
    msg = None
    if aid:
        with session_scope() as db:
            m = db.get(Message, uuid.UUID(aid))
            msg = MessageOut.model_validate(message_dict(m)) if m else None
    collapsed = [e for e in out if e["type"] != "delta"]
    return TurnResult(events=collapsed, assistant_message=msg)


@router.post("/{conv_id}/messages", dependencies=[CHAT_LIMIT], responses={200: {"content": {"text/event-stream": {}}, "model": TurnResult}})
async def send_message(conv_id: uuid.UUID, body: SendMessageIn, request: Request, user: CurrentUser, db: DB) -> Any:
    c = get_conv(db, user, conv_id)
    _validate_attachments(db, user, c, body.attachments)
    _check_model(db, c.org_id, body.model_config_id)
    t = TurnInput(c.id, user.id, body.content, list(body.attachments), body.parent_id, None, body.model_config_id, body.max_output_tokens, body.edit_of)
    db.commit()
    events = run_turn(t)
    return _sse(events, request) if body.stream else await _collect(events)


@router.post("/{conv_id}/regenerate", dependencies=[CHAT_LIMIT], responses={200: {"content": {"text/event-stream": {}}, "model": TurnResult}})
async def regenerate(conv_id: uuid.UUID, body: RegenerateIn, request: Request, user: CurrentUser, db: DB) -> Any:
    c = get_conv(db, user, conv_id)
    _check_model(db, c.org_id, body.model_config_id)
    t = TurnInput(c.id, user.id, "", [], None, body.message_id, body.model_config_id)
    db.commit()
    events = run_turn(t)
    return _sse(events, request) if body.stream else await _collect(events)


@router.get("/{conv_id}/export")
def export(conv_id: uuid.UUID, user: CurrentUser, db: DB, format: str = "md") -> Any:
    c = get_conv(db, user, conv_id)
    path = _path_to(db, c.current_leaf_id)
    if format == "json":
        return {"conversation": conversation_dict(c), "messages": [message_dict(m) for m in path]}
    if format != "md":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "format must be md or json")
    name = "".join(ch if ch.isalnum() else "-" for ch in c.title)[:60] or "conversation"
    return PlainTextResponse(export_markdown(c, path), media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
