# Security

## Threat model (summary)

MIND runs AI-generated code, fetches untrusted web pages, accepts uploads, stores provider credentials and serves several tenants. The main risks addressed are: cross-tenant data access, credential theft, code execution on the host, prompt injection that escalates permissions, SSRF, XSS through generated content, and runaway spend.

## Controls

| Area | Control | Where |
|---|---|---|
| Passwords | argon2id; constant-time dummy verify for unknown emails | `security.py`, `routers/auth.py` |
| Sessions | 30-min JWT access tokens; opaque refresh tokens stored only as SHA-256, rotated on every use; **reuse of a rotated token revokes all of the user's sessions**; password change revokes sessions | `routers/auth.py` |
| Browser transport | httpOnly `SameSite=Lax` cookies, `Secure` in production (enforced), double-submit CSRF header required on unsafe cookie-authenticated requests | `deps.current_user` |
| Authorization | Every route resolves the caller's org/project role on the server (`require_org`, `require_project`, `resolve_scope`). Foreign resources return **404** so existence does not leak. Admin-only actions (providers, models, invitations, audit, usage) require `admin`/`owner`; only owners grant ownership; the last owner cannot be removed | `deps.py`, routers |
| Tenant isolation | All tenant rows carry `org_id`; queries filter by it; private projects require explicit membership | models, routers; tested in `test_cross_org_isolation` |
| Provider credentials | Fernet-encrypted at rest (`MIND_ENCRYPTION_KEY`, required in prod); never returned by the API (only a 4-char hint); redacted from provider error messages and audit details | `security.py`, `services/audit.redact` |
| Secrets in config | No secrets in the repo; production refuses to start with the dev secret, without an encryption key or with insecure cookies | `config.py` |
| Rate limiting | Per-user/IP fixed windows (auth 10/min, chat 30/min, generation 30/min, uploads 60/min); Redis-backed when available | `deps.RateLimiter` |
| Uploads | Extension allow-list, magic-byte check for binary types, size cap, filename sanitising, optional ClamAV scan (blocked if infected) | `routers/files.py` |
| Downloads | Always `Content-Disposition: attachment` for active types (HTML/SVG/JS), `nosniff`, `CSP: sandbox`; signed URLs are HMAC-bound to one stored object and expire | `files.safe_file_response`, `artifacts.signed_download` |
| Code execution | Never on the API host. Docker containers: non-root uid 10001, read-only rootfs, `--cap-drop ALL`, `no-new-privileges`, memory/CPU/PID limits, wall-clock timeout, **no network** for runs; previews on an `--internal` network with no internet route; workspace is a temp copy; stale containers reaped | `modules/sandbox`, verified by `test_sandbox.py` and `test_builder.py` |
| Preview isolation | Separate origin (:8100), signed expiring path token, upstream only reachable on the internal network, cookies and `Set-Cookie` stripped, iframe `sandbox` without `allow-same-origin`, `frame-ancestors` limited to MIND origins | `preview_app.py`, builder page |
| SSRF | Research fetcher resolves hosts and refuses private, loopback, link-local, multicast and reserved addresses, re-checks every redirect; http(s) only; size cap; honours robots.txt | `modules/research` |
| Prompt injection | Uploaded documents, web pages and memories are wrapped as `<document>`/`<memory>` data with explicit system instructions; model output can never grant permissions: tools are limited to a fixed registry, arguments are schema-validated, risky tools always need human approval, and the Verification agent checks evidence rather than trusting tool output | `chat_service.py`, `agents.py`, `tools.py` |
| Approvals | `medium` risk: requester or admin; `high`/`critical`: org admin/owner only; decisions audited | `routers/agents.py` |
| Generated content in the UI | Markdown rendered without raw HTML; links open with `noopener noreferrer nofollow` | `components/Markdown.tsx` |
| Audit | Auth events, membership/role changes, invitations, provider/model changes, uploads, deletions, approvals, AI edits; secret-looking keys redacted | `services/audit.py`, Admin page |
| Spend | Org monthly and per-user daily budgets enforced before model calls; agent runs have budget caps and time limits; previews capped per org and stopped when idle | `services/audit.check_budget`, `agents.py`, `previews.py` |
| HTTP headers | `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options: DENY` (API), HSTS when cookies are secure | `main.py`, `next.config.ts` |

## Known gaps (tracked in ROADMAP/KNOWN_LIMITATIONS)

* Tenant isolation is enforced in application code; PostgreSQL row-level security is not yet enabled.
* **Docker socket**: the API and worker control Docker to create sandboxes. Access to the Docker socket is equivalent to root on that host. In production run sandboxes on a dedicated, isolated host (or switch the runner to gVisor/Kata/Firecracker), never on the database or API host.
* No email verification, MFA or SSO yet.
* No malware scanning unless ClamAV is installed.
* Content security policy for the web app itself is not yet strict (Next.js inline scripts).

## Memory deletion and backups

Deleting a memory removes the row and its embeddings immediately (`ON DELETE CASCADE`), so it disappears from retrieval at once. Copies inside database backups persist until those backups expire; operators should keep backup retention short (e.g. 30 days) and document it to users. Exports contain only the requesting user's own entries.

## Reporting

Please report vulnerabilities privately to the repository owner rather than in public issues.
