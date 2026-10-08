# Known limitations

Being explicit about what MIND does **not** do yet.

## Verification gaps
* **No live AI calls have been verified.** Chat, AI planning, AI edits, AI document outlines, text-to-CAD, embeddings, image generation and research synthesis are contract-tested against mocked HTTP only. They need your API key or a local OpenAI-compatible server.
* Web-search engines (Brave, Tavily, SearXNG) are contract-tested only.
* S3 storage, the Docker images and docker-compose stack, the CI workflow and the mobile app on a real device have not been exercised. (The mobile JS bundle compiles; the API image build was blocked by the development environment's network policy.)

## Product gaps
* **Video and Voice studios are not implemented** (the UI says so).
* Image studio: no inpainting/outpainting, masking, layers, background removal or upscaling.
* CAD: five parametric templates; no free-form modelling, assemblies, image-to-3D, Onshape or slicer integration. Mesh self-intersection is not checked (reported as "not checked"). Passing checks does not guarantee a good print.
* Builder: three templates; the sandbox is offline, so projects can only use the standard library and the preinstalled packages (FastAPI, uvicorn, Jinja2, SQLAlchemy, Pydantic, pytest, httpx, NumPy for Python; Node built-ins). No visual drag-and-drop editor, Git integration or deployment. Monaco loads from a CDN (falls back to a plain editor offline). Generated apps must use relative URLs because previews are served under a path prefix.
* Chat: no tool use inside chat; long attachments are truncated (120k characters) rather than retrieved by chunk; task classification for auto routing is heuristic.
* Documents: generation from specs only; editing existing uploaded documents in place is not supported; no OCR.
* Research: HTML/text sources only (no PDFs, no JavaScript-rendered pages).
* Memory: exact vector search (fine for thousands of entries; add per-model HNSW indexes for more).
* Mobile: chat is non-streaming; no uploads; limited screens.
* i18n: navigation, auth and core actions are translated to Arabic; many page-level strings are English only.
* Collaboration: conversations are private to their author.

## Operational
* Invitations and password resets have no email delivery (links are shown to the inviting admin).
* Tenant isolation is application-enforced; no Postgres RLS yet.
* The API/worker need Docker socket access to run sandboxes — root-equivalent on that host.
* First API start takes ~15 s (OpenCascade import).
