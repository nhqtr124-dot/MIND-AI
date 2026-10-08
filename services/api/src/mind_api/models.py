"""Normalized relational schema. See docs/DATABASE.md."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow

ROLES = ("owner", "admin", "editor", "viewer")
ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}
# Completion states shared by jobs, agent runs/tasks and artifacts.
STATES = (
    "planned",
    "running",
    "awaiting_approval",
    "completed",
    "failed",
    "cancelled",
    "partially_completed",
)


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _fk(target: str, nullable: bool = False, ondelete: str = "CASCADE", index: bool = True) -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index
    )


def _created() -> Mapped[datetime]:
    return mapped_column(default=utcnow, server_default=text("now()"), nullable=False)


def _states_check(col: str, name: str) -> CheckConstraint:
    return CheckConstraint(f"{col} IN ({', '.join(repr(s) for s in STATES)})", name=name)


# ---------------------------------------------------------------------------- identity & tenancy


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None]
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created()


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    user_daily_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    fallback_policy: Mapped[str] = mapped_column(String(20), default="ask", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (CheckConstraint("fallback_policy IN ('never', 'ask', 'auto')", name="ck_org_fallback"),)


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = _created()
    user: Mapped[User] = relationship(lazy="joined")
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_membership"),
        CheckConstraint("role IN ('owner', 'admin', 'editor', 'viewer')", name="ck_membership_role"),
    )


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    accepted_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    __table_args__ = (CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="ck_invitation_role"),)


# ---------------------------------------------------------------------------- projects & files


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), default="org", nullable=False)
    builder_template: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
    archived_at: Mapped[datetime | None]
    __table_args__ = (CheckConstraint("visibility IN ('org', 'private')", name="ck_project_visibility"),)


class ProjectMember(Base):
    """Explicit sharing for private projects (org admins/owners always have access)."""

    __tablename__ = "project_members"
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_project_member_role"),
    )


class ProjectFile(Base):
    """Uploaded files and Builder workspace files. Content lives in object storage."""

    __tablename__ = "project_files"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("kind IN ('upload', 'workspace')", name="ck_file_kind"),
        Index(
            "uq_workspace_path",
            "project_id",
            "path",
            unique=True,
            postgresql_where=text("kind = 'workspace'"),
        ),
    )


# ---------------------------------------------------------------------------- AI models


class ModelProvider(Base):
    __tablename__ = "model_providers"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_key_hint: Mapped[str | None] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12), default="unverified", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = _created()
    models: Mapped[list[ModelConfig]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_provider_name"),
        CheckConstraint("status IN ('unverified', 'ok', 'error')", name="ck_provider_status"),
    )


class ModelConfig(Base):
    __tablename__ = "model_configs"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    provider_id: Mapped[uuid.UUID] = _fk("model_providers.id")
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list, nullable=False)
    capabilities_source: Mapped[str] = mapped_column(String(20), default="heuristic", nullable=False)
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    quality_tier: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    speed_tier: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    input_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    price_per_image: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _created()
    provider: Mapped[ModelProvider] = relationship(back_populates="models", lazy="joined")
    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uq_model_per_provider"),
        CheckConstraint("quality_tier BETWEEN 1 AND 5 AND speed_tier BETWEEN 1 AND 5", name="ck_model_tiers"),
    )


# ---------------------------------------------------------------------------- chat


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True, ondelete="SET NULL")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    title: Mapped[str] = mapped_column(String(200), default="New chat", nullable=False)
    folder: Mapped[str | None] = mapped_column(String(80))
    model_mode: Mapped[str] = mapped_column(String(10), default="auto", nullable=False)
    model_config_id: Mapped[uuid.UUID | None] = _fk("model_configs.id", nullable=True, ondelete="SET NULL")
    preference: Mapped[str] = mapped_column(String(10), default="balanced", nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    use_memory: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_leaf_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("model_mode IN ('auto', 'manual')", name="ck_conv_mode"),)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = _uuid()
    conversation_id: Mapped[uuid.UUID] = _fk("conversations.id")
    parent_id: Mapped[uuid.UUID | None] = _fk("messages.id", nullable=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attachments: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    model_config_id: Mapped[uuid.UUID | None] = _fk("model_configs.id", nullable=True, ondelete="SET NULL")
    provider_kind: Mapped[str | None] = mapped_column(String(30))
    model_name: Mapped[str | None] = mapped_column(String(200))
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="completed", nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_message_role"),
        CheckConstraint(
            "status IN ('streaming', 'completed', 'failed', 'cancelled')", name="ck_message_status"
        ),
        Index("ix_messages_fts", text("to_tsvector('simple', content)"), postgresql_using="gin"),
    )


# ---------------------------------------------------------------------------- agents & tools


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True, ondelete="SET NULL")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    plan_source: Mapped[str] = mapped_column(String(10), nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0), nullable=False)
    time_limit_s: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    tasks: Mapped[list[AgentTask]] = relationship(
        back_populates="run", order_by="AgentTask.position", cascade="all, delete-orphan"
    )
    __table_args__ = (
        _states_check("status", "ck_run_status"),
        CheckConstraint("plan_source IN ('llm', 'user')", name="ck_run_plan_source"),
    )


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[uuid.UUID] = _uuid()
    run_id: Mapped[uuid.UUID] = _fk("agent_runs.id")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    agent: Mapped[str] = mapped_column(String(30), nullable=False)
    tool: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    run: Mapped[AgentRun] = relationship(back_populates="tasks")
    __table_args__ = (
        UniqueConstraint("run_id", "key", name="uq_task_key"),
        _states_check("status", "ck_task_status"),
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    run_id: Mapped[uuid.UUID | None] = _fk("agent_runs.id", nullable=True)
    task_id: Mapped[uuid.UUID | None] = _fk("agent_tasks.id", nullable=True)
    tool: Mapped[str] = mapped_column(String(60), nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed', 'denied')", name="ck_tool_exec_status"),
    )


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    requested_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    run_id: Mapped[uuid.UUID | None] = _fk("agent_runs.id", nullable=True)
    task_id: Mapped[uuid.UUID | None] = _fk("agent_tasks.id", nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    risk: Mapped[str] = mapped_column(String(12), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    decided_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')", name="ck_approval_status"
        ),
        CheckConstraint("risk IN ('medium', 'high', 'critical')", name="ck_approval_risk"),
    )


# ---------------------------------------------------------------------------- artifacts


class GeneratedArtifact(Base):
    __tablename__ = "generated_artifacts"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    validation_status: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
    versions: Mapped[list[ArtifactVersion]] = relationship(
        back_populates="artifact", order_by="ArtifactVersion.version", cascade="all, delete-orphan"
    )
    __table_args__ = (
        _states_check("status", "ck_artifact_status"),
        CheckConstraint(
            "kind IN ('cad', 'document', 'spreadsheet', 'presentation', 'image', 'video', 'audio', 'code', 'research_report', 'archive', 'data')",
            name="ck_artifact_kind",
        ),
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    id: Mapped[uuid.UUID] = _uuid()
    artifact_id: Mapped[uuid.UUID] = _fk("generated_artifacts.id")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = _created()
    artifact: Mapped[GeneratedArtifact] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),)


# ---------------------------------------------------------------------------- memory


class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="explicit", nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("scope IN ('user', 'project', 'team')", name="ck_memory_scope"),
        CheckConstraint("scope <> 'project' OR project_id IS NOT NULL", name="ck_memory_project"),
        Index("ix_memory_fts", text("to_tsvector('simple', content)"), postgresql_using="gin"),
    )


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"
    id: Mapped[uuid.UUID] = _uuid()
    memory_id: Mapped[uuid.UUID] = _fk("memory_entries.id")
    model_config_id: Mapped[uuid.UUID] = _fk("model_configs.id")
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("memory_id", "model_config_id", name="uq_memory_embedding"),)


# ---------------------------------------------------------------------------- automation


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = _created()


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    id: Mapped[uuid.UUID] = _uuid()
    workflow_id: Mapped[uuid.UUID] = _fk("workflows.id")
    cron: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(60), default="UTC", nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    last_run_at: Mapped[datetime | None]
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Job(Base):
    """Durable background job queue (claimed with SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    project_id: Mapped[uuid.UUID | None] = _fk("projects.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_after: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"), nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None]
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    __table_args__ = (
        _states_check("status", "ck_job_status"),
        Index("ix_jobs_claim", "status", "run_after", postgresql_where=text("status = 'planned'")),
    )


class Preview(Base):
    __tablename__ = "previews"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    project_id: Mapped[uuid.UUID] = _fk("projects.id")
    started_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    container_id: Mapped[str] = mapped_column(String(80), nullable=False)
    upstream_url: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="running", nullable=False)
    files_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = _created()
    last_access_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    stopped_at: Mapped[datetime | None]


# ---------------------------------------------------------------------------- usage, cost, audit


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_kind: Mapped[str | None] = mapped_column(String(30))
    model_name: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    units: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0), nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_usage_org_time", "org_id", "created_at"),)


class CostRecord(Base):
    """Daily roll-up maintained alongside usage events."""

    __tablename__ = "cost_records"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    day: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unpriced_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0), nullable=False)
    __table_args__ = (UniqueConstraint("org_id", "day", "category", "provider_kind", name="uq_cost_rollup"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID | None] = _fk("organizations.id", nullable=True)
    user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(60))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_audit_org_time", "org_id", "created_at"),)


class IntegrationCredential(Base):
    """Credentials for non-model integrations (web search engines, Onshape, ...)."""

    __tablename__ = "integration_credentials"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _fk("organizations.id")
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_hint: Mapped[str | None] = mapped_column(String(12))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_integration_name"),)
