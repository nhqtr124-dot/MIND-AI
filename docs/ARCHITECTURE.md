# Architecture

## Overview

```
 Browser (Next.js, :3000) ──/api/v1 rewrite──▶ FastAPI API (:8000) ──▶ PostgreSQL + pgvector
 Expo app ───────────── Bearer tokens ─────▶        │   │                    ▲
                                                    │   └── enqueue jobs ────┤ jobs table (SKIP LOCKED)
 Browser iframe ──▶ Preview proxy (:8100) ──▶ sandbox containers           │
                    (separate origin)          (internal Docker network)  Worker process(es)
                                                    ▲                       │  CAD, documents, research,
                                                    └─── docker run ────────┘  images, agents, builder
 Object storage: local filesystem (dev) or S3-compatible (prod)
 AI providers: OpenAI / Anthropic / Gemini / OpenAI-compatible via mind_ai adapters
```

One Python process type serves three roles from the same package (`mind_api`): the HTTP API, the background worker (`python -m mind_api.worker`) and the preview proxy (`mind_api.preview_app`). Pure domain logic lives in independent, separately tested Python modules under `modules/` that know nothing about HTTP or the database.

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Monorepo | pnpm workspaces (JS) + uv workspace (Python) | Shared contracts, one lockfile per ecosystem, independent module tests |
| API | FastAPI + Pydantic v2 + SQLAlchemy 2 (sync) | Typed contracts → OpenAPI → generated TS types; sync ORM is simpler and runs in the threadpool |
| Database | PostgreSQL 16 + pgvector 0.8 | Relational integrity, full-text search, vectors, and the job queue in one service |
| Auth | Self-hosted JWT access tokens (30 min, configurable) + rotating opaque refresh tokens, argon2id | Free, offline, no vendor dependency. Supabase was evaluated; it adds an external dependency without features we need yet. The auth layer is isolated in `routers/auth.py` + `deps.py` if a switch is wanted later |
| Web auth transport | httpOnly cookies via same-origin Next.js rewrite + double-submit CSRF header | Tokens not readable by page JS; CSRF enforced for cookie requests only |
| Mobile auth | Bearer tokens in SecureStore with transparent single-flight refresh | No cookies in React Native |
| Job queue | PostgreSQL table, `FOR UPDATE SKIP LOCKED`, leases, retries | Durable and resumable without another service; transactional with the data it changes. Redis is used only for rate limiting (optional) |
| Sandbox | Docker containers: no network (runs) or an `--internal` network (previews), non-root, read-only rootfs, memory/CPU/PID/time limits, dropped capabilities | Generated code never runs on the API host |
| Previews | Separate origin (:8100) + signed expiring token in the URL path + iframe `sandbox` without `allow-same-origin` | Generated JavaScript cannot reach MIND cookies or call the API as the user |
| CAD | CadQuery 2.8 (OpenCascade) for B-rep + STEP; trimesh + manifold3d for mesh checks | Real kernel, exact parametric geometry, independent validation |
| Documents | python-docx, python-pptx, openpyxl, reportlab, PyMuPDF, pycel, LibreOffice (optional) | Mature, open source; pycel evaluates formulas without Excel |
| AI providers | Thin httpx adapters, models **discovered** from each provider's list endpoint | No hardcoded model names; interchangeable; contract-tested |
| Frontend | Next.js 16 App Router, React 19, Tailwind v4, own component set (shadcn-style, no generator dependency), Monaco, react-three-fiber | Original design system; small dependency surface |
| Mobile | Expo SDK 57 / React Native 0.86 | Shared TS client and design tokens |

## Request and job flow

1. **Synchronous** endpoints validate input, check the caller's org/project role (`deps.require_org`, `deps.require_project`), and either answer directly or **enqueue a job** and return `{job_id, artifact_id}`.
2. The **worker** claims jobs, runs the handler registered with `@handler("kind")`, reports progress, and stores results. Handlers raise `JobFailed` (clean user-facing failure), `RetryableJobError` (backoff), or return `_status: partially_completed|failed`.
3. Clients poll `GET /jobs/{id}` or stream `GET /jobs/{id}/events` (SSE).
4. **Artifacts** are recorded only after files exist and have been validated (`services/artifacts.store_version` refuses missing/empty files). Each version stores file hashes, sizes and the validation report.

## Chat engine (`services/chat_service.py`)

`prepare_turn` builds the user message, a placeholder assistant message, and the path from root to the parent (conversation trees support branches). Attachments become `<document>` blocks (extracted text) or image parts; saved memories become a `<memory>` block. The system prompt marks both as untrusted data. `run_turn` then:

* **auto mode**: classifies the task (`mind_ai.router.classify`), filters enabled models by hard requirements (chat, vision, tools, context window) and ranks them by admin-assigned quality/speed tiers, configured prices and task fit; on a provider failure before any output, it tries the next ranked model.
* **manual mode**: uses only the chosen model. On failure the org `fallback_policy` decides: `never` (error), `ask` (error with alternatives — the user chooses), `auto` (try next; the switch is visible in the routing event and stored on the message).
* streams deltas as SSE, then records tokens (provider-reported or explicitly marked estimates), cost (only when prices are configured), and a usage event + daily cost roll-up.

## Agents (`services/agents.py`, `services/tools.py`)

Plans are DAGs of tool calls (`{key, tool, args, depends_on}`) produced by the Orchestrator LLM or supplied by the user. Plans are validated (known tools, schema-valid args, no cycles). Ready tasks run in parallel (thread pool). Tools have risk levels; anything above `low` creates an `approvals` row and pauses the run (`awaiting_approval`); deciding the approval re-queues the run. After each tool call the **Verification agent** checks evidence (stored files, validation status, exit codes) — a tool cannot mark itself successful. Runs end `completed`, `partially_completed`, `failed` or `cancelled`. Time limits and budget caps are enforced between steps.

## Frontend structure

`apps/web/lib` holds the API client instance, auth/org context, i18n (EN/AR with `dir` switching), theme and hooks. `components/` contains the design system, Markdown renderer (GFM, KaTeX, highlight.js, no raw HTML), STL viewer, artifact viewer with validation reports, job status and the code editor (Monaco with a textarea fallback). Each studio is a route under `app/(app)/`.

## Adapting the structure

The requested `services/workers` directory is not separate: the worker shares ORM models and services with the API, so it is `mind_api.worker` in the same package (same Docker image, different command). `modules/agents`, `modules/memory` and `modules/automation` were folded into `services/api` because they are inherently database-bound; `modules/images|video|voice` are not created yet (image logic lives in `services/image_service.py`).
