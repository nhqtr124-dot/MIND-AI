# Deployment

MIND has **not** been deployed anywhere by this project; nothing here has been exercised in production. This is the recommended path.

## Topology

| Component | Scale | Notes |
|---|---|---|
| Web (Next.js) | 1–n stateless | Proxies `/api/v1` to the API (`MIND_API_URL`) so auth cookies stay first-party |
| API (`uvicorn mind_api.main:app`) | 1–n stateless | Needs Redis for shared rate limits when n > 1 |
| Worker (`python -m mind_api.worker`) | 1–n | Jobs are claimed with `SKIP LOCKED`; scale freely |
| Preview proxy (`uvicorn mind_api.preview_app:app`) | 1–n | **Must be on a different origin** (e.g. `preview.example.com`) than the web app |
| Sandbox host | dedicated | Runs Docker for sandboxes; API/worker talk to it. Do not co-locate with the database |
| PostgreSQL 16 + pgvector | managed or self-hosted | PITR backups |
| Object storage | S3-compatible | `MIND_STORAGE_BACKEND=s3`, private bucket |
| Redis | small | rate limiting |

## Required production settings

```
MIND_ENV=prod
MIND_SECRET_KEY=<48+ random chars>
MIND_ENCRYPTION_KEY=<mind-api gen-key>      # keep in a secret manager; losing it makes stored provider keys unreadable
MIND_COOKIE_SECURE=true
MIND_CORS_ORIGINS=["https://app.example.com"]
MIND_PUBLIC_API_URL=https://app.example.com
MIND_PREVIEW_PUBLIC_URL=https://preview.example.com
MIND_ALLOW_REGISTRATION=false               # invite-only for a private team
```

The API refuses to start in `prod` with the dev secret, without an encryption key, or with insecure cookies.

## Steps

1. Provision Postgres (create extension `vector`), Redis, a private bucket.
2. Build images: `infrastructure/docker/api/Dockerfile` (API, worker, preview proxy — different commands) and `infrastructure/docker/web/Dockerfile`. The API image build could not be completed in the development sandbox because its egress proxy blocks plain-HTTP Debian mirrors; build it in normal CI.
3. Run `python -m mind_api.cli migrate` as a one-off job before each release.
4. Prepare the sandbox host: `scripts/setup-sandbox.sh`; consider gVisor (`--runtime=runsc`) for stronger isolation.
5. Put TLS-terminating reverse proxies in front of web and preview origins; forward `X-Forwarded-*`.
6. `mind-api make-admin you@example.com` for platform administration.

`infrastructure/docker/docker-compose.yml` runs the whole stack on one machine for evaluation; it mounts the Docker socket into API and worker containers, which is acceptable only for a trusted single-host setup.

## Operations

* **Monitoring**: JSON request logs with request IDs (`X-Request-ID`); `GET /api/v1/health`; job failures are visible in `jobs.error` and the UI.
* **Backups**: nightly `pg_dump` + PITR; object-store versioning; test restores quarterly. Keep backup retention aligned with the memory-deletion promise in SECURITY.md.
* **Upgrades**: migrate, then roll API/worker; workers finish or lease-expire running jobs, which are retried.
* **Approval rule**: MIND never deploys user projects; there is no deployment integration yet.
