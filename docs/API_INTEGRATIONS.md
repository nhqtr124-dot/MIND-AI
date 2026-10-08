# External API integrations

All adapters live in `modules/ai-router/src/mind_ai/providers/` and `modules/research/`. Contract tests pin the request and response shapes below. **None of them has been verified against the live service in this repository yet** (no keys were available) — see TESTING.md for the live checks.

## AI model providers

Models are never hardcoded: on save, MIND calls the provider's model-list endpoint and records what it returns. New models start disabled.

| Provider kind | Endpoints used | Auth | Notes |
|---|---|---|---|
| `openai` | `GET /v1/models`, `POST /v1/chat/completions` (`stream: true`, `stream_options.include_usage`), `POST /v1/embeddings`, `POST /v1/images/generations` | `Authorization: Bearer` | Uses `max_completion_tokens`; temperature only sent when set. Capabilities are inferred from model IDs (`heuristic`), editable by admins |
| `openai_compatible` | same paths under your base URL (e.g. `http://localhost:11434/v1` for Ollama, vLLM, LM Studio, llama.cpp, OpenRouter, Groq) | optional Bearer | Uses `max_tokens`. If the server omits usage, tokens are **estimated and labelled** as such |
| `anthropic` | `GET /v1/models` (paginated with `after_id`), `POST /v1/messages` (SSE: `message_start`, `content_block_delta`, `message_delta`, `error`) | `x-api-key`, `anthropic-version: 2023-06-01` | Images sent as base64 `image` blocks; system prompt in `system`; vision/tools capability marked by provider family |
| `gemini` | `GET /v1beta/models`, `POST /v1beta/models/{m}:streamGenerateContent?alt=sse`, `POST …:batchEmbedContents` | `x-goog-api-key` | Capabilities from `supportedGenerationMethods`; context window from `inputTokenLimit`; "thought" parts are not shown |

Errors are mapped to `auth`, `permission`, `rate_limit`, `invalid_request`, `not_found`, `unavailable`, `timeout`, `network`; the API key is redacted from messages. `rate_limit`, `unavailable`, `timeout` and `network` are retryable.

**Consumer subscriptions (ChatGPT Plus, Claude Pro, Gemini Advanced) do not include API access.** You need API keys from each provider's developer console, billed separately.

### Pricing

MIND does not ship price tables (they change). Admins enter USD per million input/output tokens and per image on each model. Calls to unpriced models are recorded as usage with `cost = NULL` and shown as "unpriced" in Admin.

## Web search

| Engine | Endpoint | Auth |
|---|---|---|
| Brave Search API | `GET https://api.search.brave.com/res/v1/web/search?q=&count=` | `X-Subscription-Token` |
| Tavily | `POST https://api.tavily.com/search` `{query, max_results}` | `Authorization: Bearer` |
| SearXNG (self-hosted, free) | `GET {base}/search?q=&format=json` | none (enable JSON format in `settings.yml`) |

Page retrieval uses MIND's own fetcher (robots.txt, SSRF protection, 3 MB cap, HTML/text only).

## Not integrated yet

Onshape, slicers, video-generation providers, speech providers, Git hosting and deployment targets — see ROADMAP.md. Adapters will follow the same rules: documented endpoints only, contract tests, and no success claims without a confirmed provider response.
