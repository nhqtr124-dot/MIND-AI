# MIND AI

A unified, multimodal AI workspace: chat with your own configured models, research with checked citations, build and preview apps in a sandbox, generate validated documents and parametric 3D parts, run agent workflows with human approval — on web (English/Arabic, RTL) and a basic mobile client, sharing one backend.

> **Status: first milestone (Phase 1–3 foundation) implemented and tested.** Video and voice studios are not implemented yet and the app says so. See [docs/FEATURE_STATUS.md](docs/FEATURE_STATUS.md) for exactly what works, what was verified and how.

MIND AI ships **without** any AI model access. Chat, AI edits, AI planning and AI document outlines need an API key (OpenAI, Anthropic, Gemini) or a local OpenAI-compatible server (Ollama, vLLM, LM Studio) that you add in **Settings → AI providers**. Everything else — CAD, documents, sandboxed code, previews, research from URLs, memory, agents with explicit plans — works without one.

## What is in the box

| Area | What it does today |
|---|---|
| **MIND Chat** | Streaming chat, auto routing (capability/quality/speed/price) or manual model choice, explicit fallback policy, file/image attachments, branching, regenerate/edit, search, Markdown/LaTeX/code, export, usage and cost per message |
| **MIND 3D** | CadQuery/OpenCascade parametric parts (Arduino UNO holder, vented/battery enclosures, L-bracket, standoff) → STL/STEP/3MF, 15-point mesh validation, BOM, Three.js viewer, AI "describe → parameters" proposal for review |
| **MIND Documents** | DOCX/PDF/PPTX/XLSX/CSV/MD/HTML/TXT from structured specs, AI outlines, block editor; every file is reopened and validated (XLSX formulas evaluated against expected values) before download |
| **MIND Builder** | Runnable templates (static web, FastAPI, Node), Monaco editor, tests and live preview in network-isolated Docker containers served from a separate origin, AI edits with test-and-repair loop, snapshots/undo, ZIP export |
| **MIND Research** | Brave/Tavily/SearXNG search or your URLs, SSRF-safe fetching honouring robots.txt, BM25 evidence extraction, cited synthesis with automatic citation/quote verification |
| **MIND Agents & Automations** | Orchestrator (LLM or explicit plans), 7 tools with risk levels, DAG execution in parallel, per-step verification, approvals, budgets/time limits, cancellation, cron workflows |
| **MIND Image** | Generation via configured OpenAI-compatible image models (decoded before storing) and real local edits with Pillow |
| **Memory, Team, Admin** | User/project/team memory with hybrid pgvector + full-text retrieval and hard delete; orgs, roles, invitations, private project sharing; usage/cost dashboards and audit log |
| **Mobile** | Expo app: sign in, workspaces, projects, artifact downloads, chat |

## Quick start (local)

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22 + pnpm 10, PostgreSQL 16 with pgvector, optional Redis, Docker (for Builder), optional LibreOffice (document previews/conversion).

```bash
cp .env.example .env                     # then set MIND_SECRET_KEY
uv sync --all-packages                   # Python workspace (.venv)
pnpm install                             # JS workspace
createdb mind && psql -d mind -c 'CREATE EXTENSION vector'
bash scripts/setup-sandbox.sh            # sandbox images + internal network (needs Docker)
bash scripts/dev.sh                      # migrate, then API :8000, preview :8100, worker, web :3000
```

Open http://localhost:3000, create an account, then add a provider in Settings. Full instructions, including Docker Compose: [docs/SETUP.md](docs/SETUP.md).

## Repository layout

```
apps/web            Next.js 16 web app (EN/AR, dark/light)
apps/mobile         Expo (React Native) client
packages/shared-types  TypeScript types generated from the API's OpenAPI schema
packages/api-client    Typed client: cookie/bearer auth, refresh, SSE streaming, uploads, jobs
packages/ui            Design tokens shared by web and mobile
services/api        FastAPI app, job worker, preview proxy, Alembic migrations, API tests
modules/ai-router   Provider adapters (OpenAI, OpenAI-compatible, Anthropic, Gemini), routing, cost
modules/cad         Parametric CAD templates, export, mesh validation
modules/documents   Document generation, extraction, conversion, validation
modules/sandbox     Docker sandbox runner and Builder templates
modules/research    Search adapters, safe fetcher, evidence ranking, citation checks
infrastructure/     Dockerfiles, docker-compose, sandbox runtime image
tests/e2e           Playwright end-to-end tests
docs/               Architecture, setup, security, testing, costs, status, roadmap
```

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Setup](docs/SETUP.md) · [Feature status](docs/FEATURE_STATUS.md) · [Testing](docs/TESTING.md) · [Security](docs/SECURITY.md) · [Database](docs/DATABASE.md) · [API integrations](docs/API_INTEGRATIONS.md) · [Deployment](docs/DEPLOYMENT.md) · [Costs](docs/COSTS.md) · [Roadmap](docs/ROADMAP.md) · [Known limitations](docs/KNOWN_LIMITATIONS.md) · [Progress log](docs/PROGRESS.md)

API reference: run the API and open http://localhost:8000/api/docs (OpenAPI at `/api/v1/openapi.json`).
