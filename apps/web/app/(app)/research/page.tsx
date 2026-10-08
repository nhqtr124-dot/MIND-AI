"use client";

import type { Artifact, Job } from "@mind/shared-types";
import { Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";
import { ArtifactView } from "@/components/ArtifactView";
import { JobStatus } from "@/components/JobStatus";
import { Alert, Button, Card, Field, PageHeader, Select, StatusBadge, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useJob, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function ResearchPage() {
  const org = useOrg();
  const { t } = useI18n();
  const engines = useLoad(async () => (await api.GET("/api/v1/integrations", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const recent = useLoad(async () => (await api.GET("/api/v1/artifacts", { params: { query: { org_id: org.id, kind: "research_report", limit: 15 } } })).data ?? [], [org.id]);
  const [question, setQuestion] = useState("");
  const [urls, setUrls] = useState("");
  const [max, setMax] = useState(6);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const onDone = useCallback(async (j: Job) => {
    const aid = (j.result as { artifact_id?: string } | null)?.artifact_id;
    if (aid) setArtifact((await api.GET("/api/v1/artifacts/{artifact_id}", { params: { path: { artifact_id: aid } } })).data ?? null);
    await recent.reload();
  }, [recent]);
  const { job, running, track } = useJob(onDone);
  const urlList = urls.split(/\s+/).filter(Boolean);

  return (
    <>
      <PageHeader icon={<Search />} title={t("nav.research")} subtitle="Search → retrieve → extract evidence → synthesise → verify citations. Evidence is kept separate from conclusions." />
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <div className="space-y-4">
          <Card>
            <div className="space-y-4">
              {engines.data && engines.data.length === 0 && <Alert tone="warning">No search engine configured. Paste source URLs below, or add Brave, Tavily or a self-hosted SearXNG in <Link href="/settings" className="underline">Settings</Link>.</Alert>}
              <Field label="Question"><Textarea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What torque do I need to lift a 2 kg robot arm segment at 30 cm?" /></Field>
              <Field label="Source URLs (optional, one per line)" hint="When set, only these pages are used. robots.txt is honoured; private network addresses are refused.">
                <Textarea rows={3} value={urls} onChange={(e) => setUrls(e.target.value)} className="font-mono text-xs" />
              </Field>
              <Field label="Max sources"><Select value={max} onChange={(e) => setMax(Number(e.target.value))}>{[3, 6, 10, 15].map((x) => <option key={x}>{x}</option>)}</Select></Field>
              {error && <Alert>{error}</Alert>}
              <Button className="w-full" loading={running} disabled={question.trim().length < 3} onClick={async () => {
                setError(null); setArtifact(null);
                try { const { data } = await api.POST("/api/v1/research", { body: { org_id: org.id, question, urls: urlList, max_sources: max } }); await track(data!.job_id); } catch (e) { setError(errorMessage(e)); }
              }}>Start research</Button>
              <JobStatus job={job} />
            </div>
          </Card>
          <Card title="Reports">
            <ul className="space-y-1">
              {recent.data?.map((a) => (
                <li key={a.id}><button className="flex w-full items-center gap-2 rounded-[10px] px-2 py-1.5 text-start text-sm hover:bg-surface-2" onClick={() => setArtifact(a)}>
                  <span className="min-w-0 flex-1 truncate">{a.title}</span><StatusBadge status={a.validation_status ?? a.status} /><span className="text-xs text-muted">{timeAgo(a.created_at)}</span>
                </button></li>
              ))}
            </ul>
          </Card>
        </div>
        <div>{artifact ? <ArtifactView artifact={artifact} onChange={setArtifact} /> : <Card><div className="grid h-80 place-items-center text-muted"><Search className="size-12 opacity-40" /></div></Card>}</div>
      </div>
    </>
  );
}
