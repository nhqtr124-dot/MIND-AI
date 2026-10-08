# Database

PostgreSQL 16 + pgvector. Schema defined in `services/api/src/mind_api/models.py`, migrations in `services/api/alembic/versions/` (`alembic check` reports no drift). All primary keys are UUIDv4; timestamps are `timestamptz`.

## Tables

| Group | Table | Purpose / notable constraints |
|---|---|---|
| Identity | `users` | unique `lower(email)`; argon2 hash; `is_platform_admin` |
| | `refresh_tokens` | SHA-256 of opaque token (unique), expiry, revocation |
| Tenancy | `organizations` | `fallback_policy ∈ {never, ask, auto}`, monthly / per-user daily budgets |
| | `memberships` | unique (org, user); role ∈ owner/admin/editor/viewer |
| | `invitations` | hashed one-time token, role ∈ admin/editor/viewer, expiry |
| Projects | `projects` | `visibility ∈ {org, private}`, optional Builder template, soft archive |
| | `project_members` | explicit sharing of private projects |
| | `project_files` | uploads and Builder workspace files; partial unique index on (project, path) for workspace files; extracted text |
| Models | `model_providers` | Fernet-encrypted key, key hint, verification status |
| | `model_configs` | discovered models: capabilities (array), source of capabilities, context window, quality/speed tiers (1–5, checked), prices, enabled |
| Chat | `conversations` | owner, mode auto/manual, preference, current branch leaf |
| | `messages` | tree via `parent_id`; status, routing JSON, tokens, cost; GIN full-text index |
| Agents | `agent_runs` | goal, plan JSON, plan source, budget, time limit, status |
| | `agent_tasks` | DAG node: tool, args, `depends_on[]`, attempts, output, verification, approval link |
| | `tool_executions` | every tool call: redacted args, status, output, duration |
| | `approvals` | risk, status, decider, note |
| Artifacts | `generated_artifacts` | kind, status, validation status, current version |
| | `artifact_versions` | files JSON (storage key, size, SHA-256, MIME), validation report, params |
| Memory | `memory_entries` | scope user/project/team (+ check that project scope has a project); GIN full-text index |
| | `memory_embeddings` | `vector` (dimension stored per row), per (memory, model) |
| Automation | `workflows`, `scheduled_jobs` | plan definition; cron, time zone, next/last run |
| Platform | `jobs` | durable queue: status, progress, attempts, lease, cancel flag; partial index for claiming |
| | `previews` | running sandbox previews per project |
| Accounting | `usage_events` | per call: tokens/units, cost (NULL when unpriced) |
| | `cost_records` | daily roll-up per org/category/provider (upserted) |
| | `audit_logs` | action, target, IP, redacted details |
| Integrations | `integration_credentials` | web-search credentials (encrypted) |

State columns on jobs, runs, tasks and artifacts share one check constraint: `planned, running, awaiting_approval, completed, failed, cancelled, partially_completed`.

## Migrations

```bash
.venv/bin/python -m mind_api.cli migrate                  # upgrade head
cd services/api && ../../.venv/bin/alembic revision --autogenerate -m "describe change"
../../.venv/bin/alembic check                            # verify models == migrations
```

## Vector search

Embeddings are stored with their dimension; queries compare only rows from the same embedding model and dimension, using exact cosine distance. With mixed models a single ANN index is not possible; for large memories add a partial HNSW index per model, e.g.
`CREATE INDEX ON memory_embeddings USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE dim = 1536;`

## Backups

Use `pg_dump`/PITR for the database and back up the object store (local `data/storage` or the S3 bucket) on the same schedule; artifacts reference storage keys. See DEPLOYMENT.md.
