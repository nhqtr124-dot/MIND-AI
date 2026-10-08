# Testing

## Suites

| Suite | Command | Needs | Count |
|---|---|---|---|
| CAD module | `cd modules/cad && ../../.venv/bin/python -m pytest -q` | — | 14 |
| Documents module | `cd modules/documents && ../../.venv/bin/python -m pytest -q` | LibreOffice (1 test skips without) | 13 |
| AI router (contract) | `cd modules/ai-router && ../../.venv/bin/python -m pytest -q` | — | 19 |
| Sandbox | `cd modules/sandbox && ../../.venv/bin/python -m pytest -q` | Docker + `scripts/setup-sandbox.sh` (skips without Docker) | 9 |
| Research | `cd modules/research && ../../.venv/bin/python -m pytest -q` | — | 12 |
| API (integration) | `cd services/api && ../../.venv/bin/python -m pytest -q` | PostgreSQL `mind_test` DB with pgvector; Docker for builder tests | 48 |
| api-client unit | `pnpm --filter @mind/api-client test` | — | 1 |
| api-client live | `MIND_API_URL=http://localhost:8000 pnpm --filter @mind/api-client test` | Running API | +1 |
| End-to-end (browser) | start the stack (`scripts/dev.sh --prod-web`), then `cd tests/e2e && npx playwright test` | Running stack, Chromium | 5 |
| Typecheck / build | `pnpm -r typecheck && pnpm --filter @mind/web build` | — | — |
| Mobile bundle | `pnpm --filter @mind/mobile bundle:check` | — | — |

The API tests drop and recreate the `public` schema of the database in `MIND_DATABASE_URL` (default `postgresql+psycopg://postgres:postgres@localhost:5432/mind_test`). **Never point it at a database you care about.**

Last full run (this repository, 2026-10-08): **115 Python tests passed, 5 Playwright tests passed, 2 api-client tests passed** (including the live one), all typechecks and the production web build passed, and the Android JS bundle compiled.

## What the mocks do and do not prove

API tests that involve AI use `FakeProvider` (`services/api/tests/conftest.py`), an OpenAI-compatible HTTP mock plugged into the **real** adapter through `httpx.MockTransport`. They prove MIND's routing, streaming, context assembly (documents, images, memory), error handling, fallback rules, accounting and storage. **They do not prove that OpenAI, Anthropic, Google or any other provider works** with your account, model or region. The same applies to image generation and web-search tests.

Everything else in the suites runs for real: PostgreSQL + pgvector, the job queue, CadQuery/OpenCascade, trimesh/manifold3d, document libraries, LibreOffice, Docker sandboxes and previews, and Chromium in the e2e suite.

## Acceptance scenarios → tests

| # | Scenario | Test | Evidence |
|---|---|---|---|
| 1 | Register, sign in, create project | `test_register_login_create_project`, e2e 1–2 | live |
| 2 | Invite, assign permissions | `test_team_invite_roles_and_permissions` | live |
| 3 | Real streamed model response | `test_streamed_response_and_accounting` | **contract only** — needs a live key (below) |
| 4 | Auto routing picks a valid configured model | `test_auto_routing_selects_valid_configured_model`, `test_router.py` | live logic, contract calls |
| 5 | Ask questions about an uploaded document | `test_document_question_answering_uses_file_contents` | contract (proves the file text reaches the model) |
| 6 | Generate and download DOCX | `test_generate_and_download_docx`, e2e 4 | live |
| 7 | XLSX with validated formulas | `test_xlsx_formulas_validated` (API + module) | live |
| 8 | Arduino holder → validated STL | `test_arduino_holder_stl`, `test_arduino_holder_matches_board_hole_pattern`, e2e 3 | live |
| 9 | Web app request → runnable source | `test_generate_run_preview_and_follow_up_edit` | live sandbox; scripted model |
| 10 | Runs in an isolated preview | same test (checks internal network, uid, read-only rootfs), e2e 5 | live |
| 11 | Follow-up prompt changes the app | same test | live sandbox; scripted model |
| 12 | Agent runs a tool and records it | `test_agent_runs_tool_and_records_execution` | live |
| 13 | Dangerous operation needs approval | `test_dangerous_operation_requires_approval` | live |
| 14 | Failed provider call → accurate error | `test_failed_provider_call_is_reported_not_faked` | contract |
| 15 | Deleted memory leaves retrieval | `test_memory_delete_removes_from_retrieval` | live (pgvector + FTS) |
| 16 | Cross-org isolation | `test_cross_org_isolation` | live |
| 17 | Image provider → downloadable image | `test_image_generation_produces_decodable_file` | **contract only** |
| 18 | Video provider → playable video | `test_unimplemented_studios_are_reported_honestly` | **not implemented**; test asserts the app says so |
| 19 | Failed CAD validation blocks "print-ready" | `test_failed_cad_validation_is_not_print_ready`, `test_open_mesh_fails_validation` | live |
| 20 | Mobile client authenticates and reads shared projects | `packages/api-client/src/integration.test.ts` | live API, same client code as the app; not run on a device |

## Live provider checks (manual, need your keys)

These are the steps to turn "contract" into "live" evidence. They cost a few cents.

1. Start the stack, sign in, add the provider in **Settings → AI providers** — a successful save means the model list call worked with your key.
2. Enable one chat model, open **MIND Chat**, send a message: tokens should stream; the message footer shows the provider-reported token counts (no "(est.)").
3. Enable an embedding model; save a memory; **Memory → search** should report `vector+fulltext`.
4. Enable an image model with a price; generate an image; check **Admin** shows the cost.
5. Record the date, provider and model in this file under "Live verification log".

### Live verification log

_No live provider verification has been recorded yet._
