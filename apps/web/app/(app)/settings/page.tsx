"use client";

import type { ModelConfig } from "@mind/shared-types";
import { CheckCircle2, RefreshCw, Settings, Trash2 } from "lucide-react";
import { useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, PageHeader, Select, StatusBadge, Tabs } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth, useOrg } from "@/lib/auth";
import { useLoad } from "@/lib/hooks";
import { useI18n, type Locale } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";

type Tab = "providers" | "models" | "integrations" | "policy" | "profile";
type ProviderKindName = "openai" | "anthropic" | "gemini" | "openai_compatible";
const CAPS = ["chat", "vision", "tools", "code", "reasoning", "long_context", "embeddings", "image_generation"] as const;

export default function SettingsPage() {
  const org = useOrg();
  const { t } = useI18n();
  const isAdmin = org.role === "owner" || org.role === "admin";
  const [tab, setTab] = useState<Tab>(isAdmin ? "providers" : "profile");
  return (
    <>
      <PageHeader icon={<Settings />} title={t("nav.settings")} subtitle={org.name} />
      <div className="mb-4">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            ...(isAdmin
              ? ([
                  { id: "providers", label: t("settings.providers") },
                  { id: "models", label: t("settings.models") },
                  { id: "integrations", label: t("settings.integrations") },
                  { id: "policy", label: t("settings.budgets") },
                ] as { id: Tab; label: string }[])
              : []),
            { id: "profile", label: "Profile" },
          ]}
        />
      </div>
      {tab === "providers" && <Providers />}
      {tab === "models" && <Models />}
      {tab === "integrations" && <Integrations />}
      {tab === "policy" && <Policy />}
      {tab === "profile" && <Profile />}
    </>
  );
}

function Providers() {
  const org = useOrg();
  const kinds = useLoad(async () => (await api.GET("/api/v1/provider-kinds")).data ?? [], []);
  const list = useLoad(async () => (await api.GET("/api/v1/providers", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [form, setForm] = useState({ kind: "openai" as ProviderKindName, name: "", base_url: "", api_key: "" });
  const [result, setResult] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const kind = kinds.data?.find((k) => k.kind === form.kind);

  return (
    <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
      <Card title="Add provider">
        <div className="space-y-3">
          <Field label="Provider">
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as ProviderKindName })}>
              {kinds.data?.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
            </Select>
          </Field>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. OpenAI (team key)" /></Field>
          <Field label="Base URL" hint={`Leave empty for ${kind?.default_base_url ?? "the default"}.`}><Input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder={kind?.default_base_url} /></Field>
          <Field label="API key" hint="Encrypted at rest and never shown again. A consumer chat subscription does not include API access.">
            <Input type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder={kind?.needs_key ? "required" : "optional"} />
          </Field>
          {result && <Alert tone={result.tone}>{result.text}</Alert>}
          <Button className="w-full" loading={busy} disabled={!form.name} onClick={async () => {
            setBusy(true); setResult(null);
            try {
              const { data } = await api.POST("/api/v1/providers", { body: { org_id: org.id, kind: form.kind, name: form.name, base_url: form.base_url || null, api_key: form.api_key || null } });
              setResult(data!.error ? { tone: "danger", text: `Saved, but verification failed: ${String(data!.error.message)}` } : { tone: "success", text: `Verified. ${data!.discovered} models discovered (${data!.added} new). Enable the ones you want under Models.` });
              setForm({ ...form, name: "", api_key: "" });
              await list.reload();
            } catch (e) { setResult({ tone: "danger", text: errorMessage(e) }); } finally { setBusy(false); }
          }}>Save & verify</Button>
        </div>
      </Card>
      <Card title="Configured providers">
        {list.data?.length ? (
          <ul className="divide-y divide-border">
            {list.data.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="font-medium">{p.name}</span><Badge>{p.kind}</Badge><StatusBadge status={p.status} />
                <span className="text-xs text-muted">{p.api_key_hint ?? "no key"} · {p.base_url ?? "default URL"}</span>
                {p.last_error && <span className="w-full text-xs text-danger">{p.last_error}</span>}
                <span className="ms-auto flex gap-1">
                  <Button size="sm" variant="ghost" icon={<RefreshCw className="size-4" />} onClick={async () => { await api.POST("/api/v1/providers/{provider_id}/verify", { params: { path: { provider_id: p.id } } }); await list.reload(); }}>Re-check</Button>
                  <Button size="sm" variant="ghost" aria-label="Delete provider" icon={<Trash2 className="size-4" />} onClick={async () => { if (confirm(`Delete ${p.name} and its models?`)) { await api.DELETE("/api/v1/providers/{provider_id}", { params: { path: { provider_id: p.id } } }); await list.reload(); } }} />
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted">No providers yet. MIND does not ship with any model access; add your own API key or a local OpenAI-compatible server (Ollama, vLLM, LM Studio).</p>}
      </Card>
    </div>
  );
}

function Models() {
  const org = useOrg();
  const models = useLoad(async () => (await api.GET("/api/v1/models", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function update(m: ModelConfig, body: Record<string, unknown>) {
    try {
      const { data } = await api.PATCH("/api/v1/models/{model_id}", { params: { path: { model_id: m.id } }, body });
      models.setData((models.data ?? []).map((x) => (x.id === m.id ? data! : x)));
    } catch (e) { setError(errorMessage(e)); }
  }
  const rows = (models.data ?? []).filter((m) => m.model_name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <Card title="Models" actions={<Input className="h-8 w-56" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />}>
      <p className="mb-3 text-sm text-muted">Discovered models start disabled. Auto mode routes only between enabled models using capabilities, the tiers you set (1–5) and the prices you enter (USD per million tokens). Capability tags marked "heuristic" were guessed from the model name — check them.</p>
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="text-xs text-muted"><tr>
            <th className="text-start">On</th><th className="text-start">Model</th><th className="text-start">Capabilities</th><th>Quality</th><th>Speed</th><th>In $/M</th><th>Out $/M</th><th>$/image</th><th>Context</th>
          </tr></thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id} className="border-t border-border align-top">
                <td className="py-2"><input type="checkbox" aria-label={`Enable ${m.model_name}`} checked={m.enabled} onChange={(e) => update(m, { enabled: e.target.checked })} className="size-4 accent-[var(--mind-primary)]" /></td>
                <td className="py-2"><div className="font-medium">{m.display_name}</div><div className="text-xs text-muted">{m.provider_name} · {m.model_name}</div></td>
                <td className="py-2">
                  <div className="flex max-w-xs flex-wrap gap-1">
                    {CAPS.map((c) => (
                      <button key={c} onClick={() => update(m, { capabilities: m.capabilities.includes(c) ? m.capabilities.filter((x) => x !== c) : [...m.capabilities, c] })}>
                        <Badge tone={m.capabilities.includes(c) ? "primary" : "muted"} className={m.capabilities.includes(c) ? "" : "opacity-40"}>{c}</Badge>
                      </button>
                    ))}
                  </div>
                  <div className="mt-1 text-[10px] text-muted">source: {m.capabilities_source}</div>
                </td>
                {(["quality_tier", "speed_tier"] as const).map((k) => (
                  <td key={k} className="py-2 text-center"><Select className="h-8 w-16" value={m[k]} onChange={(e) => update(m, { [k]: Number(e.target.value) })}>{[1, 2, 3, 4, 5].map((x) => <option key={x}>{x}</option>)}</Select></td>
                ))}
                {(["input_price_per_mtok", "output_price_per_mtok", "price_per_image"] as const).map((k) => (
                  <td key={k} className="py-2"><Input className="h-8 w-20" type="number" step="0.0001" min="0" defaultValue={m[k] ?? ""} onBlur={(e) => e.target.value !== String(m[k] ?? "") && update(m, { [k]: e.target.value === "" ? null : e.target.value })} /></td>
                ))}
                <td className="py-2 text-xs text-muted">{m.context_window?.toLocaleString() ?? "unknown"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Integrations() {
  const org = useOrg();
  const list = useLoad(async () => (await api.GET("/api/v1/integrations", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [form, setForm] = useState({ kind: "brave" as "brave" | "tavily" | "searxng", name: "", base_url: "", secret: "" });
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
      <Card title="Add web search">
        <div className="space-y-3">
          <Field label="Engine"><Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as "brave" })}><option value="brave">Brave Search API</option><option value="tavily">Tavily</option><option value="searxng">SearXNG (self-hosted, free)</option></Select></Field>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          {form.kind === "searxng" ? <Field label="Instance URL" hint="JSON output must be enabled in the instance settings."><Input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="http://searx.local:8080" /></Field>
            : <Field label="API key"><Input type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} /></Field>}
          {error && <Alert>{error}</Alert>}
          <Button className="w-full" disabled={!form.name} onClick={async () => {
            try { await api.POST("/api/v1/integrations", { body: { org_id: org.id, kind: form.kind, name: form.name, base_url: form.base_url || null, secret: form.secret || null } }); setForm({ ...form, name: "", secret: "" }); await list.reload(); } catch (e) { setError(errorMessage(e)); }
          }}>Save</Button>
        </div>
      </Card>
      <Card title="Integrations">
        <ul className="divide-y divide-border">
          {list.data?.map((i) => (
            <li key={i.id} className="flex items-center gap-2 py-2 text-sm"><span className="font-medium">{i.name}</span><Badge>{i.kind}</Badge><span className="text-xs text-muted">{i.secret_hint ?? i.base_url}</span>
              <Button size="sm" variant="ghost" className="ms-auto" aria-label="Delete" icon={<Trash2 className="size-4" />} onClick={async () => { await api.DELETE("/api/v1/integrations/{integration_id}", { params: { path: { integration_id: i.id } } }); await list.reload(); }} /></li>
          ))}
          {!list.data?.length && <p className="text-sm text-muted">No integrations. Research can still use URLs you provide.</p>}
        </ul>
      </Card>
    </div>
  );
}

function Policy() {
  const org = useOrg();
  const { reload } = useAuth();
  const [monthly, setMonthly] = useState(org.monthly_budget_usd ?? "");
  const [daily, setDaily] = useState(org.user_daily_budget_usd ?? "");
  const [fallback, setFallback] = useState(org.fallback_policy);
  const [saved, setSaved] = useState(false);
  return (
    <Card title="Budgets & model policy">
      <div className="max-w-xl space-y-4">
        <Field label="Monthly organization budget (USD)" hint="Model calls are refused once recorded spend reaches this. Calls to unpriced models are not counted."><Input type="number" min="0" step="0.01" value={String(monthly)} onChange={(e) => setMonthly(e.target.value)} /></Field>
        <Field label="Daily budget per member (USD)"><Input type="number" min="0" step="0.01" value={String(daily)} onChange={(e) => setDaily(e.target.value)} /></Field>
        <Field label="When a manually selected model fails">
          <Select value={fallback} onChange={(e) => setFallback(e.target.value as typeof fallback)}>
            <option value="ask">Ask the user to pick another model (default)</option>
            <option value="never">Show the error only</option>
            <option value="auto">Automatically try the next best model (reported on the message)</option>
          </Select>
        </Field>
        <div className="flex items-center gap-3">
          <Button onClick={async () => {
            await api.PATCH("/api/v1/orgs/{org_id}", { params: { path: { org_id: org.id } }, body: { clear_budgets: monthly === "" && daily === "", monthly_budget_usd: monthly === "" ? null : String(monthly), user_daily_budget_usd: daily === "" ? null : String(daily), fallback_policy: fallback } });
            await reload(); setSaved(true);
          }}>Save</Button>
          {saved && <span className="flex items-center gap-1 text-sm text-success"><CheckCircle2 className="size-4" /> Saved</span>}
        </div>
      </div>
    </Card>
  );
}

function Profile() {
  const { user, reload } = useAuth();
  const { locale, setLocale } = useI18n();
  const { theme, toggle } = useTheme();
  const [name, setName] = useState(user?.display_name ?? "");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card title="Profile">
        <div className="space-y-3">
          <Field label="Display name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Language"><Select value={locale} onChange={async (e) => { setLocale(e.target.value as Locale); await api.PATCH("/api/v1/auth/me", { body: { locale: e.target.value as Locale } }); }}><option value="en">English</option><option value="ar">العربية</option></Select></Field>
          <Field label="Theme"><Button variant="secondary" onClick={toggle}>{theme === "dark" ? "Switch to light" : "Switch to dark"}</Button></Field>
          <Button onClick={async () => { await api.PATCH("/api/v1/auth/me", { body: { display_name: name } }); await reload(); setMsg({ tone: "success", text: "Saved" }); }}>Save</Button>
        </div>
      </Card>
      <Card title="Change password">
        <div className="space-y-3">
          <Field label="Current password"><Input type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password" hint="At least 10 characters. All other sessions will be signed out."><Input type="password" minLength={10} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
          {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
          <Button disabled={pw.new_password.length < 10} onClick={async () => {
            try { await api.POST("/api/v1/auth/password", { body: pw }); setPw({ current_password: "", new_password: "" }); setMsg({ tone: "success", text: "Password changed" }); } catch (e) { setMsg({ tone: "danger", text: errorMessage(e) }); }
          }}>Change password</Button>
        </div>
      </Card>
    </div>
  );
}
