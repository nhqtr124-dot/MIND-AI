# Costs

MIND itself is open-source software with no licence fees. Costs come from what you run it on and which AI services you call. **There is no free unlimited AI generation**: model calls are billed by your providers.

| Category | What drives it | Free/low-cost option |
|---|---|---|
| Development | Your machine | Everything runs locally (Postgres, Redis, Docker, LibreOffice are free) |
| Hosting | API, worker, web, preview proxy | One small VM (2–4 vCPU, 8 GB RAM) handles a private team; CadQuery jobs are CPU-bound |
| Database | Postgres size, backups | Self-hosted or a small managed instance |
| Storage | Artifacts (STL/STEP/PDF/images), uploads | Local disk or a low-cost S3-compatible bucket |
| Compute (sandbox) | Builder tests/previews | Capped: 512 MB / 1 CPU per container, ≤3 previews per org, idle previews stopped after 30 min |
| AI API | Tokens and images | Local models via an OpenAI-compatible server (Ollama, vLLM) cost only your hardware |
| Web search | Search API calls | Self-hosted SearXNG, or paste URLs |

## Cost controls in the product

* Discovered models start **disabled**; admins opt in per model.
* Admin-entered prices per model → per-message cost, per-day/category/model/member dashboards.
* **Org monthly budget** and **per-member daily budget** block further paid calls once reached.
* Agent runs accept a **budget cap** and a **time limit**.
* Auto routing can prefer cheaper models (`cost` preference; `simple` tasks lean cheap).
* Jobs are cancellable; previews are limited and reaped when idle.
* Unpriced usage is still counted and flagged, never silently treated as free.

Budgets only cover calls whose prices are configured; set prices for every enabled paid model.
