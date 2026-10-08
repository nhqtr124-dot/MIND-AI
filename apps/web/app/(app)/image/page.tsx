"use client";

import type { Artifact, Job } from "@mind/shared-types";
import { Image as ImageIcon, Upload, Wand2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { ArtifactView } from "@/components/ArtifactView";
import { JobStatus } from "@/components/JobStatus";
import { Alert, Button, Card, Field, Input, PageHeader, Select, Tabs, Textarea } from "@/components/ui";
import { api, errorMessage, mind } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { useJob, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Mode = "generate" | "edit";
type Op = { op: string; width?: number; height?: number; left?: number; top?: number; degrees?: number; factor?: number };
const OPS = ["resize", "fit", "crop", "rotate", "flip", "mirror", "grayscale", "brightness", "contrast"];

export default function ImageStudio() {
  const org = useOrg();
  const { t } = useI18n();
  const [mode, setMode] = useState<Mode>("generate");
  const models = useLoad(async () => (await api.GET("/api/v1/models", { params: { query: { org_id: org.id, enabled_only: true, capability: "image_generation" } } })).data ?? [], [org.id]);
  const recent = useLoad(async () => (await api.GET("/api/v1/artifacts", { params: { query: { org_id: org.id, kind: "image", limit: 12 } } })).data ?? [], [org.id]);
  const [modelId, setModelId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [size, setSize] = useState("1024x1024");
  const [n, setN] = useState(1);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<{ id: string; name: string } | null>(null);
  const [ops, setOps] = useState<Op[]>([{ op: "resize", width: 512 }]);
  const [fmt, setFmt] = useState<"png" | "jpeg" | "webp">("png");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const onDone = useCallback(async (j: Job) => {
    const aid = (j.result as { artifact_id?: string } | null)?.artifact_id;
    if (aid) setArtifact((await api.GET("/api/v1/artifacts/{artifact_id}", { params: { path: { artifact_id: aid } } })).data ?? null);
    await recent.reload();
  }, [recent]);
  const { job, running, track } = useJob(onDone);
  const noModels = models.data && models.data.length === 0;

  return (
    <>
      <PageHeader icon={<ImageIcon />} title={t("nav.image")} subtitle="Images are shown only after the returned bytes decode as a real image." />
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <div className="space-y-4">
          <Tabs<Mode> value={mode} onChange={setMode} tabs={[{ id: "generate", label: "Generate" }, { id: "edit", label: "Edit locally" }]} />
          {mode === "generate" && (
            <Card>
              {noModels ? (
                <Alert tone="warning">No image-generation model is enabled. An admin can add an OpenAI or OpenAI-compatible provider in Settings and enable an image model. Local editing works without one.</Alert>
              ) : (
                <div className="space-y-4">
                  <Field label="Model">
                    <Select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                      <option value="">Select…</option>
                      {models.data?.map((m) => <option key={m.id} value={m.id}>{m.display_name}{m.price_per_image ? ` · $${m.price_per_image}/image` : " · price not set"}</option>)}
                    </Select>
                  </Field>
                  <Field label="Prompt"><Textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Size"><Select value={size} onChange={(e) => setSize(e.target.value)}>{["1024x1024", "1536x1024", "1024x1536", "auto"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
                    <Field label="Count"><Select value={n} onChange={(e) => setN(Number(e.target.value))}>{[1, 2, 3, 4].map((x) => <option key={x}>{x}</option>)}</Select></Field>
                  </div>
                  {error && <Alert>{error}</Alert>}
                  <Button className="w-full" icon={<Wand2 className="size-4" />} loading={running} disabled={!modelId || !prompt.trim()} onClick={async () => {
                    setError(null);
                    try { const { data } = await api.POST("/api/v1/images/generate", { body: { org_id: org.id, model_config_id: modelId, prompt, size, n } }); await track(data!.job_id); } catch (e) { setError(errorMessage(e)); }
                  }}>Generate</Button>
                  <JobStatus job={job} />
                </div>
              )}
            </Card>
          )}
          {mode === "edit" && (
            <Card>
              <div className="space-y-4">
                <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={async (e) => {
                  const f = e.target.files?.[0]; if (!f) return;
                  try { const info = await mind.upload(f, f.name, { org_id: org.id }); setSource({ id: info.id, name: info.path }); } catch (err) { setError(errorMessage(err)); }
                }} />
                <Button variant="secondary" icon={<Upload className="size-4" />} onClick={() => fileRef.current?.click()}>{source ? source.name : "Choose image"}</Button>
                {ops.map((o, i) => (
                  <div key={i} className="grid grid-cols-3 gap-2 rounded-[10px] border border-border p-2">
                    <Select value={o.op} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { op: e.target.value } : x)))}>{OPS.map((x) => <option key={x}>{x}</option>)}</Select>
                    {["resize", "fit", "crop"].includes(o.op) && <><Input type="number" placeholder="width" value={o.width ?? ""} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { ...x, width: Number(e.target.value) || undefined } : x)))} /><Input type="number" placeholder="height" value={o.height ?? ""} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { ...x, height: Number(e.target.value) || undefined } : x)))} /></>}
                    {o.op === "rotate" && <Input type="number" placeholder="degrees" value={o.degrees ?? ""} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { ...x, degrees: Number(e.target.value) } : x)))} />}
                    {["brightness", "contrast"].includes(o.op) && <Input type="number" step="0.1" placeholder="factor (1 = same)" value={o.factor ?? ""} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { ...x, factor: Number(e.target.value) } : x)))} />}
                  </div>
                ))}
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setOps([...ops, { op: "grayscale" }])}>Add step</Button>
                  {ops.length > 1 && <Button size="sm" variant="ghost" onClick={() => setOps(ops.slice(0, -1))}>Remove last</Button>}
                </div>
                <Field label="Output format"><Select value={fmt} onChange={(e) => setFmt(e.target.value as "png")}><option value="png">PNG</option><option value="jpeg">JPEG</option><option value="webp">WebP</option></Select></Field>
                {error && <Alert>{error}</Alert>}
                <Button className="w-full" loading={busy} disabled={!source} onClick={async () => {
                  setBusy(true); setError(null);
                  try { const { data } = await api.POST("/api/v1/images/edit", { body: { source_file_id: source!.id, ops, output_format: fmt } }); setArtifact(data ?? null); await recent.reload(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
                }}>Apply edits</Button>
              </div>
            </Card>
          )}
          <Card title="Recent images">
            <div className="grid grid-cols-3 gap-2">
              {recent.data?.map((a) => {
                const f = a.versions?.[a.versions.length - 1]?.files[0];
                return f ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <button key={a.id} onClick={() => setArtifact(a)}><img src={mind.artifactFileUrl(a.id, f.name, undefined, true)} alt={a.title} className="aspect-square w-full rounded-[8px] border border-border object-cover" /></button>
                ) : null;
              })}
            </div>
          </Card>
        </div>
        <div>{artifact ? <ArtifactView artifact={artifact} onChange={setArtifact} /> : <Card><div className="grid h-80 place-items-center text-muted"><ImageIcon className="size-12 opacity-40" /></div></Card>}</div>
      </div>
    </>
  );
}
