"use client";

import type { Artifact, Job } from "@mind/shared-types";
import { ArrowDown, ArrowUp, FileText, MessageSquare, Plus, Sparkles, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { ArtifactView } from "@/components/ArtifactView";
import { JobStatus } from "@/components/JobStatus";
import { Alert, Button, Card, Field, Input, PageHeader, Select, StatusBadge, Tabs, Textarea } from "@/components/ui";
import { api, errorMessage, mind } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useJob, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Mode = "ai" | "editor" | "json" | "ask";
type Kind = "document" | "presentation" | "spreadsheet";
type Format = "docx" | "pdf" | "md" | "html" | "txt" | "pptx" | "xlsx" | "csv";
const FORMATS: Record<Kind, Format[]> = { document: ["docx", "pdf", "md", "html", "txt"], presentation: ["pptx"], spreadsheet: ["xlsx", "csv"] };

type Block =
  | { type: "heading"; text: string; level: number }
  | { type: "paragraph"; text: string }
  | { type: "bullets"; items: string[]; ordered?: boolean }
  | { type: "table"; columns: string[]; rows: string[][] };

const EXAMPLES: Record<Exclude<Kind, "document">, string> = {
  presentation: JSON.stringify(
    {
      title: "Robotics Season Kickoff",
      subtitle: "Goals, plan and budget",
      slides: [
        { title: "Goals", bullets: ["Qualify for regionals", "Ship the new drivetrain by week 6"], notes: "Keep this short." },
        { title: "Budget", table: { type: "table", columns: ["Item", "Cost (USD)"], rows: [["Motors", 480], ["Sensors", 220]] } },
        { title: "Testing hours", chart: { type: "chart", kind: "bar", labels: ["Jan", "Feb", "Mar"], series: [{ name: "Hours", values: [12, 20, 31] }] } },
      ],
    },
    null,
    2,
  ),
  spreadsheet: JSON.stringify(
    {
      sheets: [
        {
          name: "BOM",
          rows: [["Part", "Qty", "Unit price", "Total"], ["Servo MG996R", 6, 9.5, "=B2*C2"], ["Arduino UNO", 1, 27, "=B3*C3"], ["Total", null, null, "=SUM(D2:D3)"]],
          charts: [{ kind: "bar", title: "Cost per part", data_range: "D1:D3", categories_range: "A2:A3", anchor: "F2" }],
        },
      ],
      expected: { "BOM!D4": 84 },
    },
    null,
    2,
  ),
};

export default function DocumentsPage() {
  const org = useOrg();
  const router = useRouter();
  const { t } = useI18n();
  const [mode, setMode] = useState<Mode>("ai");
  const [kind, setKind] = useState<Kind>("document");
  const [format, setFormat] = useState<Format>("docx");
  const [projectId, setProjectId] = useState("");
  const [prompt, setPrompt] = useState("A one-page safety checklist for a high-school robotics workshop, with a table of PPE per activity.");
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [title, setTitle] = useState("Team report");
  const [blocks, setBlocks] = useState<Block[]>([
    { type: "heading", text: "Summary", level: 1 },
    { type: "paragraph", text: "Write your content here." },
  ]);
  const [json, setJson] = useState(EXAMPLES.presentation);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("Summarize this document and list any action items.");
  const fileRef = useRef<HTMLInputElement>(null);
  const askRef = useRef<HTMLInputElement>(null);

  const projects = useLoad(async () => (await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const uploads = useLoad(async () => (await api.GET("/api/v1/files", { params: { query: projectId ? { project_id: projectId } : { org_id: org.id } } })).data ?? [], [org.id, projectId]);
  const recent = useLoad(
    async () => {
      const r = await Promise.all((["document", "presentation", "spreadsheet"] as const).map((k) => api.GET("/api/v1/artifacts", { params: { query: { org_id: org.id, kind: k, limit: 10 } } })));
      return r.flatMap((x) => x.data ?? []).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 15);
    },
    [org.id],
  );
  const lastArtifact = useRef<string | null>(null);
  const onDone = useCallback(
    async (j: Job) => {
      const aid = (j.result as { artifact_id?: string } | null)?.artifact_id ?? null;
      const id = aid ?? lastArtifact.current;
      if (id) setArtifact((await api.GET("/api/v1/artifacts/{artifact_id}", { params: { path: { artifact_id: id } } })).data ?? null);
      await recent.reload();
    },
    [recent],
  );
  const { job, running, track } = useJob(onDone);
  const scope = projectId ? { project_id: projectId } : { org_id: org.id };

  async function start(fn: () => Promise<{ job_id: string; artifact_id?: string | null } | undefined>) {
    setError(null);
    setArtifact(null);
    setBusy(true);
    try {
      const created = await fn();
      if (created) {
        lastArtifact.current = created.artifact_id ?? null;
        await track(created.job_id);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const fromPrompt = () => start(async () => (await api.POST("/api/v1/documents/from-prompt", { body: { ...scope, prompt, kind, format, source_file_ids: sourceIds } })).data);
  const fromBlocks = () => start(async () => (await api.POST("/api/v1/documents/generate", { body: { ...scope, kind: "document", format, spec: { title, blocks }, title } })).data);
  const fromJson = () =>
    start(async () => {
      let spec: Record<string, unknown>;
      try {
        spec = JSON.parse(json);
      } catch {
        throw new Error("The specification is not valid JSON");
      }
      return (await api.POST("/api/v1/documents/generate", { body: { ...scope, kind, format, spec } })).data;
    });

  async function ask(file: File) {
    setBusy(true);
    setError(null);
    try {
      const info = await mind.upload(file, file.name, scope);
      const { data: conv } = await api.POST("/api/v1/conversations", { body: { org_id: org.id, project_id: projectId || null, title: `Q&A: ${file.name}` } });
      // Ask the first question (non-streaming), then continue in the chat view.
      await api.POST("/api/v1/conversations/{conv_id}/messages", { params: { path: { conv_id: conv!.id } }, body: { content: question, attachments: [info.id], stream: false } });
      router.push(`/chat?c=${conv!.id}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  const kindFormat = (
    <div className="grid grid-cols-2 gap-3">
      <Field label="Type">
        <Select value={kind} onChange={(e) => { const k = e.target.value as Kind; setKind(k); setFormat(FORMATS[k][0]); if (k !== "document") setJson(EXAMPLES[k]); }}>
          <option value="document">Document</option>
          <option value="presentation">Presentation</option>
          <option value="spreadsheet">Spreadsheet</option>
        </Select>
      </Field>
      <Field label="Format">
        <Select value={format} onChange={(e) => setFormat(e.target.value as Format)}>
          {FORMATS[kind].map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
        </Select>
      </Field>
    </div>
  );

  return (
    <>
      <PageHeader icon={<FileText />} title={t("docs.title")} subtitle="Every file is reopened and checked against its specification before you can download it." />
      <div className="grid gap-6 xl:grid-cols-[480px_1fr]">
        <div className="space-y-4">
          <Tabs<Mode>
            value={mode}
            onChange={setMode}
            tabs={[
              { id: "ai", label: <span className="inline-flex items-center gap-1"><Sparkles className="size-3.5" /> Describe</span> },
              { id: "editor", label: "Block editor" },
              { id: "json", label: "Spec (JSON)" },
              { id: "ask", label: "Ask a file" },
            ]}
          />
          <Card>
            <div className="space-y-4">
              <Field label={t("common.project")}>
                <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                  <option value="">{t("common.noProject")}</option>
                  {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              </Field>

              {mode === "ai" && (
                <>
                  {kindFormat}
                  <Field label="What should the file contain?">
                    <Textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
                  </Field>
                  <Field label="Ground it in uploaded files (optional)" hint="The model uses only facts from these files and says when information is missing.">
                    <div className="max-h-32 space-y-1 overflow-y-auto rounded-[10px] border border-border p-2 text-sm">
                      {uploads.data?.filter((f) => f.has_text).map((f) => (
                        <label key={f.id} className="flex items-center gap-2">
                          <input type="checkbox" checked={sourceIds.includes(f.id)} onChange={(e) => setSourceIds((s) => (e.target.checked ? [...s, f.id] : s.filter((x) => x !== f.id)))} className="accent-[var(--mind-primary)]" />
                          {f.path}
                        </label>
                      ))}
                      {!uploads.data?.some((f) => f.has_text) && <span className="text-muted">No uploaded files with text.</span>}
                    </div>
                  </Field>
                  <input ref={fileRef} type="file" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) { await mind.upload(f, f.name, scope).catch((err) => setError(errorMessage(err))); await uploads.reload(); } }} />
                  <Button variant="ghost" size="sm" icon={<Upload className="size-4" />} onClick={() => fileRef.current?.click()}>Upload a source file</Button>
                  <Button className="w-full" onClick={fromPrompt} loading={busy || running} icon={<Sparkles className="size-4" />}>{t("docs.generate")}</Button>
                </>
              )}

              {mode === "editor" && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="Title"><Input value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
                    <Field label="Format">
                      <Select value={FORMATS.document.includes(format) ? format : "docx"} onChange={(e) => setFormat(e.target.value as Format)}>
                        {FORMATS.document.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
                      </Select>
                    </Field>
                  </div>
                  <BlockEditor blocks={blocks} onChange={setBlocks} />
                  <Button className="w-full" onClick={fromBlocks} loading={busy || running}>{t("docs.generate")}</Button>
                </>
              )}

              {mode === "json" && (
                <>
                  {kindFormat}
                  <Field label="Specification" hint="Spreadsheets: put Excel formulas in cells and the values they must produce in 'expected'; generation fails if they do not match.">
                    <Textarea rows={16} className="font-mono text-xs" value={json} onChange={(e) => setJson(e.target.value)} spellCheck={false} />
                  </Field>
                  <Button className="w-full" onClick={fromJson} loading={busy || running}>{t("docs.generate")}</Button>
                </>
              )}

              {mode === "ask" && (
                <>
                  <p className="text-sm text-muted">Upload a PDF, DOCX, PPTX, XLSX, CSV or text file and ask about it. Its text is extracted and given to the model as reference data.</p>
                  <Field label="Question"><Textarea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} /></Field>
                  <input ref={askRef} type="file" hidden accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.html" onChange={(e) => e.target.files?.[0] && ask(e.target.files[0])} />
                  <Button className="w-full" loading={busy} icon={<MessageSquare className="size-4" />} onClick={() => askRef.current?.click()}>Choose file and ask</Button>
                </>
              )}
              {error && <Alert>{error}</Alert>}
              <JobStatus job={job} />
              {job?.status === "failed" && (job.error as { validation?: unknown } | null)?.validation != null && (
                <Alert tone="warning">The file was generated but failed validation, so it is not offered as a finished document. Review the specification and try again.</Alert>
              )}
            </div>
          </Card>
          <Card title="Recent files">
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
            </ul>
          </Card>
        </div>
        <div>{artifact ? <ArtifactView artifact={artifact} onChange={setArtifact} /> : <Card><div className="grid h-80 place-items-center text-muted"><FileText className="size-12 opacity-40" /></div></Card>}</div>
      </div>
    </>
  );
}

function BlockEditor({ blocks, onChange }: { blocks: Block[]; onChange: (b: Block[]) => void }) {
  const set = (i: number, b: Block) => onChange(blocks.map((x, j) => (j === i ? b : x)));
  const move = (i: number, d: -1 | 1) => {
    const n = [...blocks];
    const [b] = n.splice(i, 1);
    n.splice(i + d, 0, b);
    onChange(n);
  };
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => (
        <div key={i} className="rounded-[10px] border border-border p-2">
          <div className="mb-1 flex items-center gap-1 text-xs text-muted">
            <span className="flex-1 uppercase">{b.type}</span>
            <button disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up" className="disabled:opacity-30"><ArrowUp className="size-3.5" /></button>
            <button disabled={i === blocks.length - 1} onClick={() => move(i, 1)} aria-label="Move down" className="disabled:opacity-30"><ArrowDown className="size-3.5" /></button>
            <button onClick={() => onChange(blocks.filter((_, j) => j !== i))} aria-label="Remove block" className="hover:text-danger"><Trash2 className="size-3.5" /></button>
          </div>
          {b.type === "heading" && <Input value={b.text} onChange={(e) => set(i, { ...b, text: e.target.value })} className="font-semibold" />}
          {b.type === "paragraph" && <Textarea rows={3} value={b.text} onChange={(e) => set(i, { ...b, text: e.target.value })} />}
          {b.type === "bullets" && <Textarea rows={3} value={b.items.join("\n")} onChange={(e) => set(i, { ...b, items: e.target.value.split("\n") })} placeholder="One item per line" />}
          {b.type === "table" && (
            <Textarea
              rows={4}
              className="font-mono text-xs"
              value={[b.columns.join(" | "), ...b.rows.map((r) => r.join(" | "))].join("\n")}
              onChange={(e) => {
                const lines = e.target.value.split("\n").map((l) => l.split("|").map((c) => c.trim()));
                const columns = lines[0] ?? [""];
                set(i, { ...b, columns, rows: lines.slice(1).map((r) => columns.map((_, k) => r[k] ?? "")) });
              }}
              placeholder="Header A | Header B&#10;value | value"
            />
          )}
        </div>
      ))}
      <div className="flex flex-wrap gap-1">
        {(
          [
            ["Heading", { type: "heading", text: "Heading", level: 2 }],
            ["Paragraph", { type: "paragraph", text: "" }],
            ["List", { type: "bullets", items: ["Item"] }],
            ["Table", { type: "table", columns: ["Column A", "Column B"], rows: [["", ""]] }],
          ] as [string, Block][]
        ).map(([label, b]) => (
          <Button key={label} size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => onChange([...blocks, b])}>{label}</Button>
        ))}
      </div>
    </div>
  );
}
