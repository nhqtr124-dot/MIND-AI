# Setup

## 1. Prerequisites

| Tool | Version used in development | Needed for |
|---|---|---|
| Python | 3.13 (3.12+ supported) | API, worker, modules |
| [uv](https://docs.astral.sh/uv/) | 0.11 | Python workspace |
| Node.js / pnpm | 22 / 10.28 | Web, mobile, shared packages |
| PostgreSQL | 16 with **pgvector ≥ 0.6** (built and tested with 0.8.1) | Everything |
| Redis | 7 (optional) | Shared rate limiting across API processes |
| Docker | 29 (daemon running) | Builder sandbox, previews, `code.run` tool |
| LibreOffice | 24.x (optional) | Office → PDF conversion and document page previews |
| System libs | `libgl1`, `fonts-dejavu-core` | CadQuery/OpenCascade import; Arabic PDF text |

On Debian/Ubuntu: `sudo apt-get install postgresql-16 postgresql-16-pgvector redis-server libgl1 fonts-dejavu-core libreoffice-core` (pgvector can also be built from https://github.com/pgvector/pgvector).

## 2. Configure

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into MIND_SECRET_KEY
```

All settings are `MIND_*` environment variables (see `services/api/src/mind_api/config.py`). AI provider keys are **not** environment variables: admins add them per organization in the web UI, where they are verified against the provider and stored encrypted.

## 3. Install

```bash
uv sync --all-packages       # creates .venv with the API and all modules (editable)
pnpm install                 # web, mobile, packages, e2e
```

## 4. Database

```bash
createdb mind                                  # or: psql -c 'CREATE DATABASE mind'
psql -d mind -c 'CREATE EXTENSION IF NOT EXISTS vector'
.venv/bin/python -m mind_api.cli migrate       # Alembic upgrade head
```

The migration also runs `CREATE EXTENSION IF NOT EXISTS vector`, which needs a role allowed to create extensions.

## 5. Sandbox (Builder, previews, code execution)

```bash
bash scripts/setup-sandbox.sh
```

Builds `mind-sandbox-python` (Python 3.12 + FastAPI/uvicorn/pytest/httpx/numpy — sandboxed runs are offline, so dependencies are baked in), pulls `node:22-alpine` and `python:3.12-slim`, and creates the internal network `mind-sandbox-net`. Behind a TLS-intercepting proxy, the script forwards `HTTPS_PROXY` and the CA from `SSL_CERT_FILE` as a build secret. Without Docker the rest of MIND works and the UI marks Builder as unavailable.

## 6. Run

```bash
bash scripts/dev.sh               # next dev (hot reload)
bash scripts/dev.sh --prod-web    # production build of the web app
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API + docs | http://localhost:8000/api/docs |
| Preview proxy | http://localhost:8100 |

Logs go to `data/logs/`. Run pieces individually:

```bash
.venv/bin/uvicorn mind_api.main:app --port 8000
.venv/bin/uvicorn mind_api.preview_app:app --port 8100
.venv/bin/python -m mind_api.worker
pnpm --filter @mind/web dev
```

## 7. First use

1. Register at http://localhost:3000/register (you get a personal workspace as owner).
2. **Settings → AI providers**: add a provider. For free local models run an OpenAI-compatible server such as Ollama (`ollama serve`, base URL `http://localhost:11434/v1`, no key) and choose *OpenAI-compatible*.
3. **Settings → Models**: enable the models you want, set quality/speed tiers and (optionally) prices so costs are tracked.
4. Optional: **Settings → Integrations** for web search (Brave, Tavily or self-hosted SearXNG).

## 8. Mobile

```bash
cd apps/mobile
# app.json → expo.extra.apiUrl: http://10.0.2.2:8000 (Android emulator) or your LAN IP for a device
pnpm start
```

## 9. Docker Compose

```bash
bash scripts/setup-sandbox.sh                          # network + sandbox images on the host
echo "MIND_SECRET_KEY=$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" >> .env
docker compose --env-file .env -f infrastructure/docker/docker-compose.yml up --build
```

Read the Docker-socket warning in [SECURITY.md](SECURITY.md) first.

## 10. Regenerate API types after changing the API

```bash
bash scripts/gen-api-types.sh     # OpenAPI → packages/shared-types/src/openapi.ts
```

## Troubleshooting

* `ImportError: libGL.so.1` → install `libgl1` (OpenCascade).
* `type "vector" does not exist` → install pgvector and create the extension.
* Builder says "Docker sandbox unavailable" → start the Docker daemon and run `scripts/setup-sandbox.sh`.
* Chat says no model is enabled → add a provider **and** enable at least one chat model.
* First API start is slow (~15 s) — CadQuery/OpenCascade loads at import.
