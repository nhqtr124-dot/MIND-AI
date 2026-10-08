"use client";

import type { Artifact, Job, TextToCad } from "@mind/shared-types";
import { Box, ShieldCheck, Sparkles, Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { ArtifactView, Checks } from "@/components/ArtifactView";
import { JobStatus } from "@/components/JobStatus";
import { SchemaForm, type JsonSchema } from "@/components/SchemaForm";
import { Alert, Badge, Button, Card, Field, PageHeader, Select, Spinner, StatusBadge, Tabs, Textarea } from "@/components/ui";
import { api, errorMessage, mind } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useJob, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Template = { key: string; title: string; description: string; params_schema: JsonSchema };
type Mode = "template" | "describe" | "validate";

export default function CadStudio() {
  const org = useOrg();
  const { t } = useI18n();
  const [mode, setMode] = useState<Mode>("template");
  const templates = useLoad(async () => (await api.GET("/api/v1/cad/templates")).data as unknown as Template[], []);
  const projects = useLoad(async () => (await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const recent = useLoad(async () => (await api.GET("/api/v1/artifacts", { params: { query: { org_id: org.id, kind: "cad", limit: 12 } } })).data ?? [], [org.id]);
  const [templateKey, setTemplateKey] = useState("arduino_uno_holder");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [formats, setFormats] = useState<string[]>(["stl", "step"]);
  const [projectId, setProjectId] = useState("");
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("Create a holder for an Arduino UNO with a 6 mm rim and screw holes to mount it on a robot chassis.");
  const [proposal, setProposal] = useState<TextToCad | null>(null);
  const [thinking, setThinking] = useState(false);
  const onDone = useCallback(
    async (j: Job) => {
      const aid = (j.result as { artifact_id?: string } | null)?.artifact_id;
      if (aid) setArtifact((await api.GET("/api/v1/artifacts/{artifact_id}", { params: { path: { artifact_id: aid } } })).data ?? null);
      await recent.reload();
    },
    [recent],
  );
  const { job, running, track } = useJob(onDone);
  const tpl = templates.data?.find((x) => x.key === templateKey);

  async function generate() {
    setError(null);
    setArtifact(null);
    try {
      const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== ""));
      const { data } = await api.POST("/api/v1/cad/generate", {
        body: { ...(projectId ? { project_id: projectId } : { org_id: org.id }), template: templateKey, params: clean, formats: formats as ("stl" | "step" | "3mf")[] },
      });
      await track(data!.job_id);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function describe() {
    setThinking(true);
    setError(null);
    setProposal(null);
    try {
      const { data } = await api.POST("/api/v1/cad/text-to-cad", { body: { org_id: org.id, prompt } });
      setProposal(data ?? null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setThinking(false);
    }
  }

  return (
    <>
      <PageHeader icon={<Box />} title={t("nav.3d")} subtitle="Dimension-driven parts on the OpenCascade kernel. STL/STEP/3MF are only offered after the file exists and has been validated." />
      <div className="grid gap-6 xl:grid-cols-[440px_1fr]">
        <div className="space-y-4">
          <Tabs<Mode>
            value={mode}
            onChange={setMode}
            tabs={[
              { id: "template", label: "Parameters" },
              { id: "describe", label: <span className="inline-flex items-center gap-1"><Sparkles className="size-3.5" /> Describe</span> },
              { id: "validate", label: "Check a mesh" },
            ]}
          />
          {mode === "describe" && (
            <Card title={t("cad.describe")}>
              <Textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              <p className="mt-2 text-xs text-muted">An AI model maps your description onto a parametric template and lists every assumed dimension. You review the parameters before anything is generated.</p>
              <Button className="mt-3" loading={thinking} onClick={describe} icon={<Sparkles className="size-4" />}>Propose parameters</Button>
              {proposal && (
                <div className="mt-4 space-y-2 text-sm">
                  {proposal.supported ? (
                    <Alert tone="success">Template: <b>{proposal.template}</b> (proposed by {proposal.model})</Alert>
                  ) : (
                    <Alert tone="warning">No template fits this request. {proposal.explanation}</Alert>
                  )}
                  {proposal.explanation && proposal.supported && <p className="text-muted">{proposal.explanation}</p>}
                  {proposal.assumptions.length > 0 && (
                    <div><b>Assumptions</b><ul className="list-disc ps-5 text-muted">{proposal.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></div>
                  )}
                  {proposal.missing_information.length > 0 && (
                    <div><b className="text-warning">Measurements you should confirm</b><ul className="list-disc ps-5 text-muted">{proposal.missing_information.map((a) => <li key={a}>{a}</li>)}</ul></div>
                  )}
                  {proposal.supported && proposal.template && (
                    <Button variant="secondary" onClick={() => { setTemplateKey(proposal.template!); setParams(proposal.params); setMode("template"); }}>Review & use these parameters</Button>
                  )}
                </div>
              )}
            </Card>
          )}
          {mode === "template" && (
            <Card title={t("cad.title")}>
              {templates.loading ? (
                <Spinner />
              ) : (
                <div className="space-y-4">
                  <Field label="Template">
                    <Select value={templateKey} onChange={(e) => { setTemplateKey(e.target.value); setParams({}); }}>
                      {templates.data?.map((x) => <option key={x.key} value={x.key}>{x.title}</option>)}
                    </Select>
                  </Field>
                  {tpl && <p className="text-sm text-muted">{tpl.description}</p>}
                  {tpl && <SchemaForm schema={tpl.params_schema} value={params} onChange={setParams} />}
                  <div className="flex flex-wrap gap-3 text-sm">
                    {(["stl", "step", "3mf"] as const).map((f) => (
                      <label key={f} className="flex items-center gap-1.5">
                        <input type="checkbox" checked={formats.includes(f)} disabled={f === "stl"} onChange={(e) => setFormats((cur) => (e.target.checked ? [...cur, f] : cur.filter((x) => x !== f)))} className="accent-[var(--mind-primary)]" />
                        {f.toUpperCase()}
                      </label>
                    ))}
                  </div>
                  <Field label={t("common.project")}>
                    <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                      <option value="">{t("common.noProject")}</option>
                      {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </Select>
                  </Field>
                  {error && <Alert>{error}</Alert>}
                  <Button className="w-full" onClick={generate} loading={running} disabled={org.role === "viewer"}>{t("cad.generate")}</Button>
                  <JobStatus job={job} />
                </div>
              )}
            </Card>
          )}
          {mode === "validate" && <MeshCheck />}
          <Card title="Recent parts">
            <ul className="space-y-1">
              {recent.data?.map((a) => (
                <li key={a.id}>
                  <button className="flex w-full items-center gap-2 rounded-[10px] px-2 py-1.5 text-start text-sm hover:bg-surface-2" onClick={() => setArtifact(a)}>
                    <span className="min-w-0 flex-1 truncate">{a.title}</span>
                    <StatusBadge status={a.validation_status ?? a.status} />
                    <span className="text-xs text-muted">{timeAgo(a.created_at)}</span>
                  </button>
                </li>
              ))}
              {!recent.data?.length && <p className="text-sm text-muted">{t("common.empty")}</p>}
            </ul>
          </Card>
        </div>
        <div>{artifact ? <ArtifactView artifact={artifact} onChange={setArtifact} /> : <Card><div className="grid h-[420px] place-items-center text-muted"><Box className="size-12 opacity-40" /></div></Card>}</div>
      </div>
    </>
  );
}

function MeshCheck() {
  const org = useOrg();
  const ref = useRef<HTMLInputElement>(null);
  const [report, setReport] = useState<{ status: string; checks: { id: string; label: string; status: string; detail: string }[]; disclaimer: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dims, setDims] = useState("");
  return (
    <Card title={<span className="flex items-center gap-2"><ShieldCheck className="size-4" /> Validate an existing mesh</span>}>
      <p className="mb-3 text-sm text-muted">Upload an STL, 3MF or OBJ to run the same integrity and printability checks used for generated parts.</p>
      <Field label="Expected size in mm (optional, e.g. 80 x 50 x 20)">
        <input value={dims} onChange={(e) => setDims(e.target.value)} className="h-10 w-full rounded-[10px] border border-border bg-surface-2/60 px-3 text-sm" placeholder="L x W x H" />
      </Field>
      <input ref={ref} type="file" accept=".stl,.3mf,.obj" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        if (!f) return;
        setBusy(true); setError(null); setReport(null);
        try {
          const up = await mind.upload(f, f.name, { org_id: org.id });
          const parts = dims.split(/[x×,\s]+/).map(Number).filter((n) => n > 0);
          const { data } = await api.POST("/api/v1/cad/validate", { body: { file_id: up.id, expected_extents: parts.length === 3 ? (parts as [number, number, number]) : null } });
          setReport(data as never);
        } catch (err) {
          setError(errorMessage(err));
        } finally {
          setBusy(false);
        }
      }} />
      <Button className="mt-3" variant="secondary" loading={busy} icon={<Upload className="size-4" />} onClick={() => ref.current?.click()}>Choose mesh file</Button>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
      {report && (
        <div className="mt-4 space-y-2">
          <div className="flex items-center gap-2"><Badge tone={report.status === "validation_failed" ? "danger" : report.status === "printable_with_warnings" ? "warning" : "success"}>{report.status.replaceAll("_", " ")}</Badge></div>
          <Checks checks={report.checks} />
          <p className="text-xs text-warning">{report.disclaimer}</p>
        </div>
      )}
    </Card>
  );
}
