"""Public API contracts. The TypeScript client types are generated from these via OpenAPI."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["owner", "admin", "editor", "viewer"]
State = Literal["planned", "running", "awaiting_approval", "completed", "failed", "cancelled", "partially_completed"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------- auth


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    locale: Literal["en", "ar"] = "en"
    invitation_token: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str | None = None


class UserOut(ORM):
    id: uuid.UUID
    email: str
    display_name: str
    locale: str
    is_platform_admin: bool
    created_at: datetime


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=120)
    locale: Literal["en", "ar"] | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    csrf_token: str
    user: UserOut


# ---------------------------------------------------------------------------- orgs


class OrgOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    is_personal: bool
    monthly_budget_usd: Decimal | None
    user_daily_budget_usd: Decimal | None
    fallback_policy: Literal["never", "ask", "auto"]
    created_at: datetime


class OrgWithRole(OrgOut):
    role: Role


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    monthly_budget_usd: Decimal | None = Field(None, ge=0)
    user_daily_budget_usd: Decimal | None = Field(None, ge=0)
    fallback_policy: Literal["never", "ask", "auto"] | None = None
    clear_budgets: bool = False


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role
    joined_at: datetime


class RoleChange(BaseModel):
    role: Role


class InviteIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "editor", "viewer"] = "editor"


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    token: str | None = Field(None, description="Only returned once, at creation. Share the accept link with the invitee.")
    accept_url: str | None = None


class AcceptInviteIn(BaseModel):
    token: str


# ---------------------------------------------------------------------------- projects & files


class ProjectCreate(BaseModel):
    org_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    description: str = Field("", max_length=4000)
    visibility: Literal["org", "private"] = "org"
    builder_template: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    description: str | None = Field(None, max_length=4000)
    visibility: Literal["org", "private"] | None = None


class ProjectOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str
    visibility: Literal["org", "private"]
    builder_template: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ProjectWithRole(ProjectOut):
    role: str


class ProjectShare(BaseModel):
    user_id: uuid.UUID
    role: Literal["editor", "viewer"]


class FileOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    kind: str
    path: str
    size_bytes: int
    mime_type: str
    sha256: str
    has_text: bool = False
    extraction_error: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------- jobs & artifacts


class JobOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    kind: str
    status: State
    progress: int
    message: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ArtifactFileOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    size_bytes: int
    sha256: str
    mime_type: str
    format: str | None = None


class ArtifactVersionOut(BaseModel):
    version: int
    files: list[ArtifactFileOut]
    validation: dict[str, Any] | None
    params: dict[str, Any] | None
    created_at: str


class ArtifactOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    kind: str
    title: str
    status: State
    validation_status: str | None
    source: dict[str, Any]
    current_version: int
    created_at: str
    updated_at: str
    versions: list[ArtifactVersionOut] = []


class JobCreated(BaseModel):
    job_id: uuid.UUID
    artifact_id: uuid.UUID | None = None


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int


class ArtifactMove(BaseModel):
    project_id: uuid.UUID | None


# ---------------------------------------------------------------------------- providers & models


class ProviderKindOut(BaseModel):
    kind: str
    label: str
    needs_key: bool
    default_base_url: str


class ProviderCreate(BaseModel):
    org_id: uuid.UUID
    kind: Literal["openai", "anthropic", "gemini", "openai_compatible"]
    name: str = Field(min_length=1, max_length=80)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)


class ProviderUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    base_url: str | None = None
    api_key: str | None = None


class ProviderOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    base_url: str | None
    api_key_hint: str | None
    status: Literal["unverified", "ok", "error"]
    last_error: str | None
    last_checked_at: datetime | None
    created_at: datetime


class ProviderCreated(BaseModel):
    provider: ProviderOut
    discovered: int
    added: int
    error: dict[str, Any] | None = None


class ModelConfigOut(ORM):
    id: uuid.UUID
    provider_id: uuid.UUID
    model_name: str
    display_name: str
    capabilities: list[str]
    capabilities_source: str
    context_window: int | None
    max_output_tokens: int | None
    quality_tier: int
    speed_tier: int
    input_price_per_mtok: Decimal | None
    output_price_per_mtok: Decimal | None
    price_per_image: Decimal | None
    enabled: bool
    provider_kind: str = ""
    provider_name: str = ""
    provider_status: str = ""


Capability = Literal["chat", "vision", "tools", "embeddings", "image_generation", "code", "reasoning", "long_context"]


class ModelConfigUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=200)
    capabilities: list[Capability] | None = None
    context_window: int | None = Field(None, ge=256)
    max_output_tokens: int | None = Field(None, ge=16)
    quality_tier: int | None = Field(None, ge=1, le=5)
    speed_tier: int | None = Field(None, ge=1, le=5)
    input_price_per_mtok: Decimal | None = Field(None, ge=0)
    output_price_per_mtok: Decimal | None = Field(None, ge=0)
    price_per_image: Decimal | None = Field(None, ge=0)
    enabled: bool | None = None


# ---------------------------------------------------------------------------- chat


class ConversationCreate(BaseModel):
    org_id: uuid.UUID
    project_id: uuid.UUID | None = None
    title: str | None = Field(None, max_length=200)
    folder: str | None = Field(None, max_length=80)
    model_mode: Literal["auto", "manual"] = "auto"
    model_config_id: uuid.UUID | None = None
    preference: Literal["balanced", "quality", "speed", "cost"] = "balanced"
    system_prompt: str | None = Field(None, max_length=8000)
    use_memory: bool = True


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    folder: str | None = Field(None, max_length=80)
    project_id: uuid.UUID | None = None
    model_mode: Literal["auto", "manual"] | None = None
    model_config_id: uuid.UUID | None = None
    preference: Literal["balanced", "quality", "speed", "cost"] | None = None
    system_prompt: str | None = Field(None, max_length=8000)
    use_memory: bool | None = None
    current_leaf_id: uuid.UUID | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    folder: str | None
    model_mode: Literal["auto", "manual"]
    model_config_id: uuid.UUID | None
    preference: str
    system_prompt: str | None
    use_memory: bool
    current_leaf_id: uuid.UUID | None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    role: Literal["user", "assistant", "system"]
    content: str
    attachments: list[str]
    status: Literal["streaming", "completed", "failed", "cancelled"]
    error: dict[str, Any] | None
    model_config_id: uuid.UUID | None
    provider_kind: str | None
    model_name: str | None
    routing: dict[str, Any] | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: str | None
    created_at: str


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    parent_id: uuid.UUID | None = Field(None, description="Message to reply under; defaults to the conversation's current leaf")
    edit_of: uuid.UUID | None = Field(None, description="Edit a previous user message: the new message becomes its sibling")
    model_config_id: uuid.UUID | None = Field(None, description="One-turn override, e.g. after accepting a fallback model")
    max_output_tokens: int = Field(2048, ge=16, le=64000)
    stream: bool = True


class RegenerateIn(BaseModel):
    message_id: uuid.UUID
    model_config_id: uuid.UUID | None = None
    stream: bool = True


class TurnResult(BaseModel):
    events: list[dict[str, Any]]
    assistant_message: MessageOut | None


class SearchHitOut(BaseModel):
    conversation_id: uuid.UUID
    conversation_title: str
    message_id: uuid.UUID
    role: str
    snippet: str
    created_at: str


# ---------------------------------------------------------------------------- agents & approvals


class RunCreate(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    goal: str = Field(min_length=1, max_length=20_000)
    plan: dict[str, Any] | None = Field(None, description="Optional explicit plan {tasks:[...]}; omitted -> the Orchestrator plans with an LLM")
    budget_usd: Decimal | None = Field(None, ge=0)
    time_limit_s: int = Field(900, ge=10, le=7200)


class RunCreated(BaseModel):
    job_id: uuid.UUID
    run_id: uuid.UUID


class ApprovalOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID
    requested_by: uuid.UUID | None
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    action: str
    risk: str
    summary: str
    details: dict[str, Any]
    status: Literal["pending", "approved", "rejected", "expired"]
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=2000)


# ---------------------------------------------------------------------------- memory


class MemoryCreate(BaseModel):
    org_id: uuid.UUID
    content: str = Field(min_length=1, max_length=8000)
    scope: Literal["user", "project", "team"] = "user"
    project_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class MemoryUpdate(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=8000)
    tags: list[str] | None = None


class MemoryOut(BaseModel):
    id: uuid.UUID
    scope: str
    project_id: uuid.UUID | None
    content: str
    source: str
    tags: list[str]
    user_id: uuid.UUID
    created_at: str
    updated_at: str


class MemorySearchHit(MemoryOut):
    score: float
    method: str


# ---------------------------------------------------------------------------- integrations & research


class IntegrationCreate(BaseModel):
    org_id: uuid.UUID
    kind: Literal["brave", "tavily", "searxng"]
    name: str = Field(min_length=1, max_length=80)
    base_url: str | None = None
    secret: str | None = None


class IntegrationOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    base_url: str | None
    secret_hint: str | None
    enabled: bool
    created_at: datetime


class ResearchCreate(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    question: str = Field(min_length=3, max_length=2000)
    urls: list[str] = Field(default_factory=list, max_length=20)
    max_sources: int = Field(6, ge=1, le=15)
    engine: str | None = None


# ---------------------------------------------------------------------------- generation requests


class CadGenerateIn(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    template: str
    params: dict[str, Any] = Field(default_factory=dict)
    formats: list[Literal["stl", "step", "3mf"]] = ["stl", "step"]
    title: str | None = Field(None, max_length=200)
    artifact_id: uuid.UUID | None = Field(None, description="Add a new version to an existing CAD artifact")


class TextToCadIn(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    prompt: str = Field(min_length=3, max_length=4000)


class TextToCadOut(BaseModel):
    template: str | None
    params: dict[str, Any]
    explanation: str
    assumptions: list[str]
    missing_information: list[str]
    supported: bool
    model: str


class DocumentGenerateIn(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    kind: Literal["document", "presentation", "spreadsheet"] = "document"
    format: Literal["docx", "pdf", "md", "html", "txt", "pptx", "xlsx", "csv"] = "docx"
    spec: dict[str, Any]
    title: str | None = None


class DocumentFromPromptIn(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    prompt: str = Field(min_length=3, max_length=8000)
    kind: Literal["document", "presentation", "spreadsheet"] = "document"
    format: Literal["docx", "pdf", "md", "html", "txt", "pptx", "xlsx", "csv"] = "docx"
    source_file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)


class ImageGenerateIn(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    model_config_id: uuid.UUID
    prompt: str = Field(min_length=1, max_length=4000)
    size: str = Field("1024x1024", pattern=r"^\d{2,4}x\d{2,4}$|^auto$")
    n: int = Field(1, ge=1, le=4)


class ImageEditIn(BaseModel):
    source_file_id: uuid.UUID | None = None
    source_artifact_id: uuid.UUID | None = None
    source_file_name: str | None = None
    project_id: uuid.UUID | None = None
    ops: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    output_format: Literal["png", "jpeg", "webp"] = "png"
    title: str | None = None


# ---------------------------------------------------------------------------- builder


class BuilderInitIn(BaseModel):
    template: str


class FileWriteIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=1_000_000)


class FileRenameIn(BaseModel):
    path: str
    new_path: str


class WorkspaceFileOut(BaseModel):
    path: str
    size_bytes: int
    sha256: str
    mime_type: str
    updated_at: datetime


class WorkspaceFileContent(WorkspaceFileOut):
    content: str | None
    binary: bool


class AiEditIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=8000)


class PreviewOut(BaseModel):
    id: uuid.UUID
    url: str
    status: str
    reused: bool
    started_at: datetime


# ---------------------------------------------------------------------------- workflows


class WorkflowCreate(BaseModel):
    org_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    definition: dict[str, Any] = Field(description="Agent plan {tasks:[...]} executed on each trigger")
    cron: str | None = Field(None, description="5-field cron expression; omit for manual-only workflows")
    timezone: str = "UTC"


class WorkflowOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    definition: dict[str, Any]
    enabled: bool
    cron: str | None
    timezone: str | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------------------- admin


class UsageSummary(BaseModel):
    org_id: uuid.UUID
    since: datetime
    total_cost_usd: Decimal
    unpriced_events: int
    by_day: list[dict[str, Any]]
    by_category: list[dict[str, Any]]
    by_model: list[dict[str, Any]]
    by_user: list[dict[str, Any]]


class AuditOut(ORM):
    id: uuid.UUID
    org_id: uuid.UUID | None
    user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    details: dict[str, Any]
    created_at: datetime
