"use client";

import type { Memory } from "@mind/shared-types";
import { Brain, Download, Trash2 } from "lucide-react";
import { useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function MemoryPage() {
  const org = useOrg();
  const { t } = useI18n();
  const list = useLoad(async () => (await api.GET("/api/v1/memory", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const projects = useLoad(async () => (await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<"user" | "team" | "project">("user");
  const [projectId, setProjectId] = useState("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<(Memory & { score: number; method: string })[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shown: (Memory & { method?: string })[] = hits ?? list.data ?? [];

  return (
    <>
      <PageHeader icon={<Brain />} title={t("nav.memory")} subtitle="Only what you explicitly save is remembered. Deleting removes the entry and its embeddings from retrieval immediately." actions={
        <a href={`/api/v1/memory/export?org_id=${org.id}`} download="mind-memory.json"><Button variant="secondary" icon={<Download className="size-4" />}>Export mine</Button></a>
      } />
      <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
        <Card title="Remember something">
          <div className="space-y-3">
            <Textarea rows={4} value={content} onChange={(e) => setContent(e.target.value)} placeholder="Our team prints in PETG with 0.4 mm nozzles." />
            <Field label="Visible to">
              <Select value={scope} onChange={(e) => setScope(e.target.value as "user")}>
                <option value="user">Only me</option>
                <option value="team">Everyone in {org.name}</option>
                <option value="project">People in a project</option>
              </Select>
            </Field>
            {scope === "project" && <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}><option value="">Choose…</option>{projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>}
            {error && <Alert>{error}</Alert>}
            <Button className="w-full" disabled={!content.trim()} onClick={async () => {
              try { await api.POST("/api/v1/memory", { body: { org_id: org.id, content, scope, project_id: scope === "project" ? projectId : null } }); setContent(""); await list.reload(); } catch (e) { setError(errorMessage(e)); }
            }}>{t("common.save")}</Button>
          </div>
        </Card>
        <Card title="Memories" actions={
          <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (!q.trim()) return setHits(null); const { data } = await api.GET("/api/v1/memory/search", { params: { query: { org_id: org.id, q } } }); setHits(data ?? []); }}>
            <Input className="h-8 w-56" placeholder={t("common.search")} value={q} onChange={(e) => setQ(e.target.value)} />
            {hits && <Button size="sm" variant="ghost" type="button" onClick={() => { setHits(null); setQ(""); }}>Clear</Button>}
          </form>
        }>
          {shown.length ? (
            <ul className="divide-y divide-border">
              {shown.map((m) => (
                <li key={m.id} className="flex items-start gap-3 py-2 text-sm">
                  <Badge tone={m.scope === "team" ? "accent" : m.scope === "project" ? "primary" : "muted"}>{m.scope}</Badge>
                  <div className="min-w-0 flex-1"><p>{m.content}</p><p className="text-xs text-muted">{m.source} · {timeAgo(m.created_at)}{m.method ? ` · matched by ${m.method}` : ""}</p></div>
                  <button aria-label="Delete memory" className="text-muted hover:text-danger" onClick={async () => {
                    try { await api.DELETE("/api/v1/memory/{memory_id}", { params: { path: { memory_id: m.id } } }); setHits((h) => h?.filter((x) => x.id !== m.id) ?? null); await list.reload(); } catch (e) { setError(errorMessage(e)); }
                  }}><Trash2 className="size-4" /></button>
                </li>
              ))}
            </ul>
          ) : <Empty title={t("common.empty")} />}
        </Card>
      </div>
    </>
  );
}
