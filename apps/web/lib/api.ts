import { MindClient } from "@mind/api-client";

/** Browser client: same-origin requests (Next.js proxies /api/v1), httpOnly cookies + CSRF header. */
export const mind = new MindClient({
  baseUrl: "",
  auth: "cookie",
  onUnauthorized: () => {
    const PUBLIC = ["/login", "/register", "/invite"];
    if (typeof window !== "undefined" && !PUBLIC.some((p) => window.location.pathname.startsWith(p))) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    }
  },
});

export const api = mind.api;

export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
