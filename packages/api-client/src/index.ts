import createClient, { type Client, type Middleware } from "openapi-fetch";
import type { ChatEvent, Job, JobState, TokenOut, paths } from "@mind/shared-types";
import { TERMINAL_STATES } from "@mind/shared-types";
import { parseSSE } from "./sse";

export { parseSSE };
export type * from "@mind/shared-types";

export interface TokenStore {
  get(): Promise<{ access: string; refresh: string } | null>;
  set(tokens: { access: string; refresh: string } | null): Promise<void>;
}

export interface MindClientOptions {
  /** Origin of the API, e.g. "http://localhost:8000". Use "" in the web app (Next.js proxies /api). */
  baseUrl: string;
  /** "cookie" for browsers (httpOnly cookies + CSRF header), "bearer" for mobile and scripts. */
  auth: "cookie" | "bearer";
  tokenStore?: TokenStore;
  fetch?: typeof fetch;
  onUnauthorized?: () => void;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : formatDetail(detail) || `HTTP ${status}`);
  }
}

function formatDetail(detail: unknown): string {
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ");
  return detail ? JSON.stringify(detail) : "";
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
/** Endpoints where a 401 means "bad credentials", not "access token expired". */
const NO_REFRESH = /\/auth\/(login|register|refresh|logout)$/;

export class MindClient {
  readonly api: Client<paths>;
  private refreshing: Promise<boolean> | null = null;
  private readonly fetchImpl: typeof fetch;

  constructor(private readonly opts: MindClientOptions) {
    this.fetchImpl = opts.fetch ?? globalThis.fetch.bind(globalThis);
    this.api = createClient<paths>({
      baseUrl: opts.baseUrl,
      fetch: (req: Request) => this.send(req),
      credentials: opts.auth === "cookie" ? "include" : "omit",
    });
    const unwrap: Middleware = {
      async onResponse({ response }) {
        if (!response.ok) {
          let detail: unknown = response.statusText;
          try {
            detail = (await response.clone().json()).detail;
          } catch {
            /* non-JSON error body */
          }
          throw new ApiError(response.status, detail);
        }
        return undefined;
      },
    };
    this.api.use(unwrap);
  }

  /** Low-level fetch with auth headers, CSRF and a single transparent token refresh. */
  async send(req: Request, retry = true): Promise<Response> {
    const prepared = await this.authorize(req.clone());
    const res = await this.fetchImpl(prepared);
    if (res.status === 401 && retry && !NO_REFRESH.test(new URL(req.url, "http://x").pathname)) {
      if (await this.refresh()) return this.send(req, false);
      this.opts.onUnauthorized?.();
    }
    return res;
  }

  private async authorize(req: Request): Promise<Request> {
    const headers = new Headers(req.headers);
    if (this.opts.auth === "bearer") {
      const t = await this.opts.tokenStore?.get();
      if (t) headers.set("Authorization", `Bearer ${t.access}`);
    } else if (UNSAFE.has(req.method)) {
      const csrf = readCookie("mind_csrf");
      if (csrf) headers.set("X-CSRF-Token", csrf);
    }
    return new Request(req, { headers });
  }

  private refresh(): Promise<boolean> {
    if (!this.refreshing) {
      this.refreshing = (async () => {
        try {
          const stored = this.opts.auth === "bearer" ? await this.opts.tokenStore?.get() : null;
          if (this.opts.auth === "bearer" && !stored) return false;
          const res = await this.fetchImpl(`${this.opts.baseUrl}/api/v1/auth/refresh`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: this.opts.auth === "cookie" ? "include" : "omit",
            body: JSON.stringify(stored ? { refresh_token: stored.refresh } : {}),
          });
          if (!res.ok) {
            await this.opts.tokenStore?.set(null);
            return false;
          }
          const data = (await res.json()) as TokenOut;
          await this.opts.tokenStore?.set({ access: data.access_token, refresh: data.refresh_token });
          return true;
        } catch {
          return false;
        } finally {
          setTimeout(() => (this.refreshing = null), 0);
        }
      })();
    }
    return this.refreshing;
  }

  // ------------------------------------------------------------------ auth helpers

  async login(email: string, password: string): Promise<TokenOut> {
    const { data } = await this.api.POST("/api/v1/auth/login", { body: { email, password } });
    await this.opts.tokenStore?.set({ access: data!.access_token, refresh: data!.refresh_token });
    return data!;
  }

  async register(body: { email: string; password: string; display_name: string; locale?: "en" | "ar"; invitation_token?: string | null }): Promise<TokenOut> {
    const { data } = await this.api.POST("/api/v1/auth/register", { body: { locale: "en", ...body } });
    await this.opts.tokenStore?.set({ access: data!.access_token, refresh: data!.refresh_token });
    return data!;
  }

  async logout(): Promise<void> {
    const stored = await this.opts.tokenStore?.get();
    try {
      await this.api.POST("/api/v1/auth/logout", { body: stored ? { refresh_token: stored.refresh } : {} });
    } finally {
      await this.opts.tokenStore?.set(null);
    }
  }

  // ------------------------------------------------------------------ streaming chat

  /** Send a chat message and receive server-sent events as they arrive. */
  async *streamMessage(
    conversationId: string,
    body: { content: string; attachments?: string[]; parent_id?: string | null; edit_of?: string | null; model_config_id?: string | null; max_output_tokens?: number },
    signal?: AbortSignal,
  ): AsyncGenerator<ChatEvent> {
    yield* this.streamPost(`/api/v1/conversations/${conversationId}/messages`, { ...body, stream: true }, signal);
  }

  async *streamRegenerate(conversationId: string, messageId: string, modelConfigId?: string | null, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    yield* this.streamPost(`/api/v1/conversations/${conversationId}/regenerate`, { message_id: messageId, model_config_id: modelConfigId ?? null, stream: true }, signal);
  }

  private async *streamPost(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    const req = new Request(`${this.opts.baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      credentials: this.opts.auth === "cookie" ? "include" : "omit",
      signal,
    });
    const res = await this.send(req);
    if (!res.ok || !res.body) {
      let detail: unknown = res.statusText;
      try {
        detail = (await res.json()).detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, detail);
    }
    for await (const ev of parseSSE(res.body)) yield JSON.parse(ev.data) as ChatEvent;
  }

  // ------------------------------------------------------------------ files & jobs

  async upload(file: Blob, name: string, scope: { org_id?: string; project_id?: string }) {
    const form = new FormData();
    form.append("file", file, name);
    if (scope.org_id) form.append("org_id", scope.org_id);
    if (scope.project_id) form.append("project_id", scope.project_id);
    const res = await this.send(
      new Request(`${this.opts.baseUrl}/api/v1/files`, { method: "POST", body: form, credentials: this.opts.auth === "cookie" ? "include" : "omit" }),
    );
    const data = await res.json();
    if (!res.ok) throw new ApiError(res.status, data.detail);
    return data as import("@mind/shared-types").FileInfo;
  }

  /** Poll a job until it reaches a terminal state. */
  async waitForJob(jobId: string, onUpdate?: (job: Job) => void, opts: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {}): Promise<Job> {
    const deadline = Date.now() + (opts.timeoutMs ?? 15 * 60_000);
    for (;;) {
      const { data } = await this.api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } });
      onUpdate?.(data!);
      if (TERMINAL_STATES.includes(data!.status as JobState)) return data!;
      if (opts.signal?.aborted) throw new Error("aborted");
      if (Date.now() > deadline) throw new Error("timed out waiting for job");
      await new Promise((r) => setTimeout(r, opts.intervalMs ?? 1000));
    }
  }

  /** Authenticated URL for an artifact file (cookie mode) — use signedUrl() for sharing or mobile. */
  artifactFileUrl(artifactId: string, name: string, version?: number, inline = false): string {
    const q = new URLSearchParams();
    if (version) q.set("version", String(version));
    if (inline) q.set("inline", "true");
    return `${this.opts.baseUrl}/api/v1/artifacts/${artifactId}/files/${encodeURIComponent(name)}${q.size ? `?${q}` : ""}`;
  }

  async signedUrl(artifactId: string, name: string, version?: number): Promise<string> {
    const { data } = await this.api.POST("/api/v1/artifacts/{artifact_id}/files/{name}/signed-url", {
      params: { path: { artifact_id: artifactId, name }, query: version ? { version } : {} },
    });
    return data!.url;
  }
}

export function memoryTokenStore(): TokenStore {
  let t: { access: string; refresh: string } | null = null;
  return { get: async () => t, set: async (v) => void (t = v) };
}
