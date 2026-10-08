# Progress log (for resuming work)

Branch: `claude/mind-ai-platform-t7k1a3`. Read FEATURE_STATUS.md first; it is the source of truth for what works.

## Session 1 — 2026-10-08

Environment found: empty repo; Python 3.13, Node 22, pnpm 10, Postgres 16 (pgvector built from source, 0.8.1), Redis 7, Docker 29 (daemon started manually: `dockerd &`), FFmpeg, LibreOffice. **No AI provider keys; Ollama and Hugging Face blocked by egress policy** → no live model verification possible.

Built and verified:
* Python modules (`modules/*`) with 67 tests; API (`services/api`) with 48 integration tests on real Postgres + Docker.
* Web app (all studios), production build OK; Playwright e2e 5/5 against the running stack.
* Shared TS packages; api-client unit + live integration test.
* Expo mobile app; Android JS bundle compiles.
* Docs, CI workflow, Dockerfiles, compose (not executed end to end). Building the API image here failed because the egress proxy blocks plain-HTTP Debian apt mirrors (and ghcr.io blobs; uv is now installed from PyPI instead).

Bugs found by tests and fixed: audit() kwarg clash broke approvals; chat edits could not branch from the root (added `edit_of`); `/register` redirected to `/login`; api-client never refreshed tokens on `/auth/me`.

## How to resume in a fresh container

```bash
pg_ctlcluster 16 main start          # or your Postgres service
redis-server --daemonize yes
(dockerd > /tmp/dockerd.log 2>&1 &)  # if the Docker daemon is not running
uv sync --all-packages && pnpm install
bash scripts/setup-sandbox.sh
# pgvector may need rebuilding from source in a new container (see SETUP.md)
bash scripts/dev.sh --prod-web
```

## Suggested next tasks (in order)

1. Verify one live provider with a real key and record it in TESTING.md.
2. Run the CI workflow on GitHub and fix anything environment-specific.
3. Postgres RLS policies; SMTP invitations.
4. Phase 4 items from ROADMAP.md (video via FFmpeg first — FFmpeg is already available locally).
