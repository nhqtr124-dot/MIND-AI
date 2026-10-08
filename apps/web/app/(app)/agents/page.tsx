"use client";

import type { Approval } from "@mind/shared-types";
import { Bot, Check, ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, PageHeader, StatusBadge, Tabs, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Task = { id: string; key: string; agent_title: string; tool: string; title: string; status: string; error: string | null; verification: { passed: boolean; detail: string } | null; depends_on: string[]; output: Record<string, unknown> | null; attempts: number };
type Run = { id: string; goal: string; status: string; plan_source: string; error: string | null; created_at: string; tasks?: Task[]; result?: { artifacts?: string[] } | null };
type Tool = { name: string; agent_title: string; description: string; risk: string; requires_approval: boolean; available: boolean; unavailable_reason: string | null };

const EXAMPLE = JSON.stringify(
  { tasks: [
    { key: "holder", tool: "cad.generate_part", title: "Arduino holder", args: { template: "arduino_uno_holder", params: { rim_height: 6 }, title: "Arduino holder" } },
    { key: "report", tool: "documents.generate", title: "Build notes", depends_on: ["holder"], args: { format: "pdf", title: "Build notes", spec: { title: "Arduino holder build notes", blocks: [{ type: "paragraph", text: "Part status: {{holder.validation_status}}" }] } } },
  ] }, null, 2);

export default function AgentsPage() {
  const org = useOrg();
  const { t } = useI18n();
  const [mode, setMode] = useState<"goal" | "plan">("goal");
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(EXAMPLE);
  const [budget, setBudget] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tools = useLoad(async () => (await api.GET("/api/v1/agents/tools")).data as unknown as Tool[], []);
  const runs = useLoad(async () => (await api.GET("/api/v1/agents/runs", { params: { query: { org_id: org.id } } })).data as unknown as Run[], [org.id]);
  const approvals = useLoad(async () => (await api.GET("/api/v1/approvals", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [run, setRun] = useState<Run | null>(null);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    const tick = async () => {
      const { data } = await api.GET("/api/v1/agents/runs/{run_id}", { params: { path: { run_id: selected } } });
      if (!alive) return;
      setRun(data as unknown as Run);
      const s = (data as unknown as Run).status;
      if (s === "planned" || s === "running") setTimeout(tick, 1200);
      else { void runs.reload(); void approvals.reload(); }
    };
    void tick();
    return () => { alive = false; };
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  async function create() {
    setError(null);
    try {
      const body: Record<string, unknown> = { org_id: org.id, goal: mode === "goal" ? goal : goal || "Explicit plan", budget_usd: budget ? budget : null };
      if (mode === "plan") body.plan = JSON.parse(plan);
      const { data } = await api.POST("/api/v1/agents/runs", { body: body as never });
      setSelected(data!.run_id);
      await runs.reload();
    } catch (e) {
      setError(e instanceof SyntaxError ? "Plan is not valid JSON" : errorMessage(e));
    }
  }

  async function decide(a: Approval, decision: "approve" | "reject") {
    try {
      await api.POST("/api/v1/approvals/{approval_id}", { params: { path: { approval_id: a.id } }, body: { decision } });
      await approvals.reload();
      if (a.run_id) setSelected(null), setTimeout(() => setSelected(a.run_id!), 0);
    } catch (e) { setError(errorMessage(e)); }
  }

  return (
    <>
      <PageHeader icon={<Bot />} title={t("nav.agents")} subtitle="The Orchestrator plans with the tools below; each step is verified independently. Risky steps wait for a human." />
      {!!approvals.data?.length && (
        <Card className="mb-6 border-warning/50" title={<span className="flex items-center gap-2"><ShieldAlert className="size-4 text-warning" /> Waiting for approval</span>}>
          <ul className="space-y-2">
            {approvals.data.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={a.risk === "medium" ? "warning" : "danger"}>{a.risk}</Badge>
                <span className="min-w-0 flex-1">{a.summary}</span>
                <code className="max-w-full truncate text-xs text-muted">{JSON.stringify(a.details.args)}</code>
                <Button size="sm" icon={<Check className="size-4" />} onClick={() => decide(a, "approve")}>Approve</Button>
                <Button size="sm" variant="danger" icon={<X className="size-4" />} onClick={() => decide(a, "reject")}>Reject</Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <div className="space-y-4">
          <Card title="New run">
            <div className="space-y-3">
              <Tabs value={mode} onChange={setMode} tabs={[{ id: "goal", label: "Goal (AI plans)" }, { id: "plan", label: "Explicit plan" }]} />
              <Field label="Goal"><Textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Design a mounting bracket for a 40 mm fan and write a one-page PDF with its dimensions." /></Field>
              {mode === "plan" && <Field label="Plan (JSON)" hint="Use {{task.field}} to pass outputs between steps."><Textarea rows={12} className="font-mono text-xs" value={plan} onChange={(e) => setPlan(e.target.value)} spellCheck={false} /></Field>}
              <Field label="Budget cap (USD, optional)"><Input type="number" step="0.01" min="0" value={budget} onChange={(e) => setBudget(e.target.value)} /></Field>
              {error && <Alert>{error}</Alert>}
              <Button className="w-full" onClick={create} disabled={mode === "goal" && !goal.trim()}>Start</Button>
            </div>
          </Card>
          <Card title="Tools">
            <ul className="space-y-2 text-sm">
              {tools.data?.map((tl) => (
                <li key={tl.name}>
                  <div className="flex items-center gap-2"><code className="text-xs">{tl.name}</code>{tl.requires_approval && <Badge tone="warning">needs approval</Badge>}{!tl.available && <Badge tone="danger">unavailable</Badge>}</div>
                  <div className="text-xs text-muted">{tl.agent_title} — {tl.description}{tl.unavailable_reason ? ` (${tl.unavailable_reason})` : ""}</div>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-4">
          {run && (
            <Card title={<span className="flex items-center gap-2">{run.goal.slice(0, 80)} <StatusBadge status={run.status} /></span>}>
              {run.error && <Alert>{run.error}</Alert>}
              <ol className="mt-2 space-y-2">
                {run.tasks?.map((tk) => (
                  <li key={tk.id} className="rounded-[12px] border border-border p-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={tk.status} />
                      <span className="font-medium">{tk.title}</span>
                      <code className="text-xs text-muted">{tk.tool}</code>
                      <span className="ms-auto text-xs text-muted">{tk.agent_title}{tk.depends_on.length ? ` · after ${tk.depends_on.join(", ")}` : ""}</span>
                    </div>
                    {tk.verification && <div className={`mt-1 text-xs ${tk.verification.passed ? "text-success" : "text-danger"}`}>Verification: {tk.verification.detail}</div>}
                    {tk.error && <div className="mt-1 text-xs text-danger">{tk.error}</div>}
                    {tk.output && <details className="mt-1"><summary className="cursor-pointer text-xs text-muted">Output</summary><pre className="mt-1 max-h-48 overflow-auto rounded-[8px] bg-surface-2 p-2 text-xs">{JSON.stringify(tk.output, null, 2)}</pre></details>}
                  </li>
                ))}
              </ol>
              {(run.status === "running" || run.status === "awaiting_approval" || run.status === "planned") && (
                <Button className="mt-3" variant="danger" size="sm" onClick={async () => { await api.POST("/api/v1/agents/runs/{run_id}/cancel", { params: { path: { run_id: run.id } } }); setSelected(null); setTimeout(() => setSelected(run.id), 0); }}>Cancel run</Button>
              )}
            </Card>
          )}
          <Card title="Runs">
            <ul className="space-y-1">
              {runs.data?.map((r) => (
                <li key={r.id}><button onClick={() => setSelected(r.id)} className="flex w-full items-center gap-2 rounded-[10px] px-2 py-1.5 text-start text-sm hover:bg-surface-2">
                  <StatusBadge status={r.status} /><span className="min-w-0 flex-1 truncate">{r.goal}</span><Badge>{r.plan_source}</Badge><span className="text-xs text-muted">{timeAgo(r.created_at)}</span>
                </button></li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
