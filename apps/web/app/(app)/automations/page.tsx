"use client";

import { Play, Trash2, Workflow } from "lucide-react";
import { useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

const DEFAULT = JSON.stringify({ tasks: [{ key: "check", tool: "research.fetch_url", title: "Check competition rules page", args: { url: "https://example.org/" } }] }, null, 2);

export default function AutomationsPage() {
  const org = useOrg();
  const { t } = useI18n();
  const flows = useLoad(async () => (await api.GET("/api/v1/workflows", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 7 * * 1");
  const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [def, setDef] = useState(DEFAULT);
  const [msg, setMsg] = useState<{ tone: "danger" | "success"; text: string } | null>(null);

  return (
    <>
      <PageHeader icon={<Workflow />} title={t("nav.automations")} subtitle="A workflow is an agent plan run manually or on a schedule. Risky steps still pause for approval." />
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <Card title="New workflow">
          <div className="space-y-3">
            <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Schedule (cron)" hint="Leave empty for manual only. 0 7 * * 1 = Mondays 07:00."><Input value={cron} onChange={(e) => setCron(e.target.value)} className="font-mono" /></Field>
              <Field label="Time zone"><Input value={tz} onChange={(e) => setTz(e.target.value)} /></Field>
            </div>
            <Field label="Plan (JSON)"><Textarea rows={10} className="font-mono text-xs" value={def} onChange={(e) => setDef(e.target.value)} /></Field>
            {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
            <Button className="w-full" disabled={!name.trim()} onClick={async () => {
              try {
                await api.POST("/api/v1/workflows", { body: { org_id: org.id, name, definition: JSON.parse(def), cron: cron.trim() || null, timezone: tz } });
                setMsg({ tone: "success", text: "Workflow saved." }); setName(""); await flows.reload();
              } catch (e) { setMsg({ tone: "danger", text: e instanceof SyntaxError ? "Plan is not valid JSON" : errorMessage(e) }); }
            }}>Save workflow</Button>
          </div>
        </Card>
        <Card title="Workflows">
          {flows.data?.length ? (
            <ul className="divide-y divide-border">
              {flows.data.map((w) => (
                <li key={w.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span className="font-medium">{w.name}</span>
                  {w.cron ? <Badge tone="accent">{w.cron} {w.timezone}</Badge> : <Badge>manual</Badge>}
                  {!w.enabled && <Badge tone="warning">paused</Badge>}
                  <span className="text-xs text-muted">{w.next_run_at ? `next ${new Date(w.next_run_at).toLocaleString()}` : ""}{w.last_run_at ? ` · last ${new Date(w.last_run_at).toLocaleString()}` : ""}</span>
                  <span className="ms-auto flex gap-1">
                    <Button size="sm" variant="secondary" icon={<Play className="size-4" />} onClick={async () => { await api.POST("/api/v1/workflows/{wf_id}/run", { params: { path: { wf_id: w.id } } }); setMsg({ tone: "success", text: "Run started — see MIND Agents." }); }}>Run now</Button>
                    <Button size="sm" variant="ghost" onClick={async () => { await api.PATCH("/api/v1/workflows/{wf_id}", { params: { path: { wf_id: w.id }, query: { enabled: !w.enabled } } }); await flows.reload(); }}>{w.enabled ? "Pause" : "Resume"}</Button>
                    <Button size="sm" variant="ghost" aria-label="Delete" icon={<Trash2 className="size-4" />} onClick={async () => { if (confirm(`Delete ${w.name}?`)) { await api.DELETE("/api/v1/workflows/{wf_id}", { params: { path: { wf_id: w.id } } }); await flows.reload(); } }} />
                  </span>
                </li>
              ))}
            </ul>
          ) : <Empty title="No workflows yet" />}
        </Card>
      </div>
    </>
  );
}
