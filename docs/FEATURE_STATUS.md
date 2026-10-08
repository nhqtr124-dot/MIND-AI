# Feature status

Legend — **Status**: ✅ implemented · 🟡 partial · ⛔ not implemented.
**Verified** describes the strongest evidence that exists *today*:
- **live** – exercised against the real component (real Postgres, real Docker, real CadQuery, real LibreOffice, real browser).
- **contract** – request/response shapes tested against a mocked HTTP transport. This proves MIND builds documented requests and parses documented responses; it does **not** prove the external service works with your account.
- **none** – not executed.

No AI provider API keys were available while building, and the build environment could not reach a local model runtime. **No live LLM, image-model or web-search call has been made.** Everything that depends on them is contract-tested only.

| Feature | Status | Provider / library | Credentials | Verified | Tests | Known limitations | Next step |
|---|---|---|---|---|---|---|---|
| Accounts, login, refresh rotation, CSRF | ✅ | FastAPI, PyJWT, argon2-cffi | — | live | `test_auth_teams.py`, e2e | No email verification / password reset email (no mail service) | SMTP adapter + verification flow |
| Organizations, roles, invitations, private project sharing | ✅ | PostgreSQL | — | live | `test_auth_teams.py`, api-client integration | Invites are shared as links (no email sending) | Email delivery, SSO/OAuth |
| Tenant isolation | ✅ | Backend checks in `deps.py`; 404 for foreign resources | — | live | `test_cross_org_isolation` | Enforced in application code, not Postgres RLS | Add RLS policies as defence in depth |
| Projects, file upload, text extraction | ✅ | PyMuPDF, python-docx/pptx, openpyxl | — | live | `test_upload_validation`, chat tests | No OCR (scanned PDFs report "no text layer"); ClamAV used only if installed | Tesseract OCR adapter |
| Object storage + signed downloads | ✅ (local) / 🟡 (S3) | Filesystem; boto3 for S3 | S3 keys for S3 | live (local); none (S3) | `test_generate_and_download_docx` | S3 backend untested | MinIO-backed integration test |
| Job queue, worker, progress, cancel, retries, lease recovery | ✅ | PostgreSQL `SKIP LOCKED` | — | live | studio/agent/builder tests | Single-region; no priority classes | Priorities, per-org concurrency caps |
| Chat streaming (SSE) | ✅ | `mind_ai` adapters | Provider API key | contract | `test_chat.py` (FakeProvider) | Not exercised against a live model | Run `docs/TESTING.md` live checks with a key |
| OpenAI / OpenAI-compatible adapter | ✅ | Chat Completions, `/models`, embeddings, images | API key (optional for local servers) | contract | `test_providers.py` | Capability tags guessed from model IDs (labelled "heuristic", admin-editable) | Live smoke test |
| Anthropic adapter | ✅ | Messages API (`anthropic-version: 2023-06-01`), `/v1/models` | API key | contract | `test_providers.py` | No tool-use / extended features yet | Tool use for agents |
| Gemini adapter | ✅ | `generativelanguage.googleapis.com/v1beta` streamGenerateContent, models, batchEmbedContents | API key | contract | `test_providers.py` | No image generation via Gemini | — |
| Model discovery & registry | ✅ | Provider list endpoints | API key | contract | `test_chat.py`, `test_providers.py` | Discovered models start disabled (deliberate) | — |
| Auto routing / manual selection / fallback policy | ✅ | `mind_ai.router` | — | live (logic) + contract (calls) | `test_router.py`, `test_chat.py` | Task classification is heuristic (regex), not model-based | Learn from feedback |
| Attachments (docs, images) in chat | ✅ | Extracted text / base64 images | — | contract | `test_document_question_answering…`, `test_image_attachment…` | Long documents are truncated (120k chars), no chunked RAG over a single file yet | Chunk + retrieve for long files |
| Branching, regenerate, edit, search, export | ✅ | PostgreSQL FTS | — | live | `test_branching…` | Search is `simple` text config (no stemming) | Language-aware FTS |
| Usage, costs, budgets | ✅ | `usage_events`, `cost_records` | — | live | `test_budget_blocks_spend`, image test | Costs only for models with admin-entered prices | Price import helper |
| Memory (user/project/team), hybrid retrieval, hard delete | ✅ | pgvector cosine + FTS, reciprocal rank fusion | Embedding model (optional) | live (FTS, pgvector) + contract (embeddings) | `test_agents_memory.py` | Exact vector scan (no ANN index: mixed dimensions) | Per-dimension HNSW indexes |
| Agents: planning, DAG, parallelism, verification, approvals, budgets, cancel | ✅ | `services/agents.py`, 7 tools | Chat model for LLM planning | live (explicit plans), contract (LLM planning) | `test_agents_memory.py` | Tools are first-party only; no external-account tools | More tools (git, deploy with approval) |
| Automations (cron workflows) | ✅ | croniter, worker scheduler | — | live | `test_workflow_manual_and_scheduled` | No event triggers (only cron/manual); no credential vault for external services | Webhook triggers |
| Research | ✅ | Brave, Tavily, SearXNG; SSRF-safe fetcher; BM25; citation checks | Search API key (or own URLs) | live (fetch logic on mocked pages), contract (search engines + synthesis) | `test_research.py`, `test_research_from_urls…` | PDF sources not parsed; no JS rendering | PDF ingestion |
| Documents: DOCX/PDF/PPTX/XLSX/CSV/MD/HTML/TXT + validation | ✅ | python-docx, reportlab, python-pptx, openpyxl, pycel | — | live | `test_docs.py`, `test_studios.py`, e2e | Templates/styles are basic; editing an *existing* uploaded document is not supported | Template library, in-place edits |
| AI document outlines | ✅ | LLM → validated spec | Chat model | contract | (shares chat/LLM path) | — | — |
| Document conversion + previews | ✅ | LibreOffice headless, PyMuPDF | — | live | `test_docx_to_pdf_conversion` | Requires LibreOffice installed | — |
| Arabic/RTL documents | ✅ | arabic-reshaper + python-bidi + DejaVu Sans; DOCX `w:bidi` | — | live | `test_arabic_pdf_and_docx` | Needs a font with Arabic glyphs | — |
| CAD templates → STL/STEP/3MF | ✅ | CadQuery 2.8 (OpenCascade), trimesh 3MF | — | live | `test_cad.py`, `test_arduino_holder_stl`, e2e | 5 templates; no free-form CAD scripting | Sandboxed CadQuery scripts from the CAD agent |
| Mesh validation & print-ready gating | ✅ | trimesh, manifold3d, BRepCheck | — | live | `test_cad.py`, `test_failed_cad_validation…` | Self-intersection not checked at mesh level (stated in report) | — |
| Text-to-CAD (description → parameters) | ✅ | LLM mapped onto templates, user reviews | Chat model | none (no live model) | — | Limited to existing templates | More templates |
| 3D viewer | ✅ | three.js / react-three-fiber | — | live (browser) | e2e | No exploded views / assemblies | Assemblies |
| Image-to-3D, Onshape, slicer | ⛔ | — | — | — | — | — | See ROADMAP |
| Builder: templates, editor, tests, previews, AI edits, snapshots, ZIP | ✅ | Docker sandbox, Monaco | Chat model for AI edits | live (sandbox, previews, tests), contract (AI edits) | `test_builder.py`, `test_sandbox.py`, e2e | Templates: static web, FastAPI, Node; no npm/pip installs in sandbox (offline); Monaco loads from CDN (textarea fallback) | Dependency install step with approval; more stacks |
| Visual drag-and-drop editor, Git integration, deployment | ⛔ | — | — | — | — | — | See ROADMAP |
| Image generation | ✅ | OpenAI-compatible `/images/generations` | API key | contract | `test_image_generation…`, `test_image_provider_garbage…` | Not tested against a live image model | Live smoke test |
| Image editing | 🟡 | Pillow (resize, fit, crop, rotate, flip, mirror, grayscale, brightness, contrast, format) | — | live | `test_local_image_edit` | No inpainting/outpainting/background removal/upscaling/layers | Provider edit endpoints, rembg |
| Video studio | ⛔ | (FFmpeg present on dev machine) | — | — | `test_unimplemented_studios…` checks honest status | UI states "not implemented" | Phase 4 |
| Voice studio | ⛔ | — | — | — | same | UI states "not implemented" | Phase 4 |
| Web app (EN/AR, RTL, themes, responsive) | ✅ | Next.js 16, Tailwind 4 | — | live (production build + Playwright) | e2e (5 tests) | Arabic translations cover navigation and key UI; many page strings are English only | Complete translations |
| Mobile app | 🟡 | Expo SDK 57 | — | Android JS bundle compiles; client logic tested against live API | api-client integration test | Not run on an emulator/device; chat is non-streaming; no file upload yet | Emulator smoke test, uploads |
| Docker Compose / images | 🟡 | Dockerfiles in `infrastructure/docker` | — | API image build attempted (see PROGRESS.md) | — | Compose stack not brought up end to end | Full compose smoke test |
| CI | 🟡 | GitHub Actions `.github/workflows/ci.yml` | — | none (not yet run on GitHub) | — | — | Watch first run |
