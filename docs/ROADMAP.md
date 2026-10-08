# Roadmap

Ordered by value and dependency. "Done" items are verified per FEATURE_STATUS.md.

## Phase 0–3 — done (first milestone)

Monorepo, API, database, auth, teams, projects, storage, job queue, design system, web app (EN/AR), basic mobile app, shared API client; provider adapters, streaming chat, auto/manual routing, attachments, agents, memory, research; documents, sandboxed code execution, live previews, AI edits, image generation and local edits, parametric STL/STEP, downloads; tests and documentation.

## Next (Phase 3 hardening)

1. **Live provider verification** with real keys (see TESTING.md) and recording results.
2. PostgreSQL **row-level security** policies mirroring the application checks.
3. Email delivery (invitations, verification, password reset) via SMTP adapter.
4. Long-document Q&A: chunk + embed uploads, retrieve relevant chunks instead of truncating.
5. Full Arabic translation of all page strings; language-aware full-text search.
6. Mobile: emulator/device smoke tests, uploads, streaming via XHR progress events.
7. Bring up docker-compose end to end in CI; S3/MinIO integration test.
8. Self-host Monaco assets (no CDN).

## Phase 4 — advanced studios

* **MIND Video**: storyboard/script generation; provider adapters for text/image-to-video (after verifying current APIs and pricing); FFmpeg pipeline (trim, concat, transitions, captions/subtitles, voiceover, aspect presets); ffprobe validation of duration/resolution/streams; per-job cost estimate and approval before paid generation.
* **MIND Voice**: speech-to-text and text-to-speech adapters (OpenAI audio endpoints, optional local Whisper/Piper), consent-gated microphone capture, transcripts; no voice cloning of real people.
* **Image**: provider edit/inpainting endpoints, masking canvas with layers and undo, background removal (rembg), upscaling.
* **CAD**: more templates (gears, hinges, cable clips, servo mounts, assemblies with BOM), sandboxed CadQuery scripts written by the CAD agent, exploded views, Onshape API (OAuth, document creation confirmed by response), slicer integration (PrusaSlicer CLI) for print-time estimates, image-assisted modelling with explicit "visual approximation" labelling.
* **Builder**: dependency install step (network, with approval), more stacks (Next.js, Expo), visual editor persisting to source via an intermediate component tree, Git push/PR integration.
* Team collaboration: shared conversations, comments on artifacts.

## Phase 5 — automation and production

* Event/webhook triggers, credential vault for external services, sandboxed browser automation (Playwright in the sandbox, approval for submissions).
* Deployment integrations (Vercel/Fly/Render) — explicit approval, URL reported only after a confirmed deployment.
* gVisor/Firecracker sandbox runtime; dedicated sandbox host.
* Metrics (Prometheus/OpenTelemetry), alerting, load testing, backup/restore drills.
* Strict CSP, MFA, SSO (OIDC).
