"use client";

import type { Job, WorkspaceFile } from "@mind/shared-types";
import { Bot, Download, ExternalLink, File, FilePlus, FlaskConical, History, Play, RefreshCw, Save, Square, Trash2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CodeEditor } from "@/components/CodeEditor";
import { JobStatus } from "@/components/JobStatus";
import { Alert, Badge, Button, Modal, Spinner, StatusBadge, Tabs, Textarea, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useJob, useLoad, timeAgo } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";

type Center = "code" | "preview";
type TestResult = { ran: boolean; passed?: boolean; stdout?: string; stderr?: string; reason?: string; command?: string[]; duration_s?: number };
type AiResult = { summary?: string; attempts?: { attempt: number; model?: string; files?: string[]; tests_passed?: boolean; error?: string }[]; tests?: TestResult };

export default function Workspace() {
  const { id } = useParams<{ id: string }>();
  const { theme } = useTheme();
  const project = useLoad(async () => (await api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: id } } })).data!, [id]);
  const files = useLoad(async () => (await api.GET("/api/v1/builder/projects/{project_id}/files", { params: { path: { project_id: id } } })).data ?? [], [id]);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [binary, setBinary] = useState(false);
  const [center, setCenter] = useState<Center>("code");
  const [preview, setPreview] = useState<{ url: string; nonce: number } | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [log, setLog] = useState<{ at: string; kind: string; job: Job }[]>([]);
  const [snapshotsOpen, setSnapshotsOpen] = useState(false);
  const readOnly = project.data?.role === "viewer";

  const tree = useMemo(() => (files.data ?? []).slice().sort((a, b) => a.path.localeCompare(b.path)), [files.data]);

  const open = useCallback(
    async (path: string) => {
      const { data } = await api.GET("/api/v1/builder/projects/{project_id}/file", { params: { path: { project_id: id }, query: { path } } });
      setActive(path);
      setBinary(Boolean(data?.binary));
      setContent(data?.content ?? "");
      setSaved(data?.content ?? "");
      setCenter("code");
    },
    [id],
  );

  useEffect(() => {
    if (!active && tree.length) void open(tree.find((f) => /index\.html$|main\.py$|server\.js$/.test(f.path))?.path ?? tree[0].path);
  }, [tree, active, open]);

  useEffect(() => {
    api.GET("/api/v1/builder/projects/{project_id}/preview", { params: { path: { project_id: id } } }).then(({ data }) => data && setPreview({ url: data.url, nonce: Date.now() }));
  }, [id]);

  const onJobDone = useCallback(
    async (j: Job) => {
      setLog((l) => [{ at: new Date().toISOString(), kind: j.kind, job: j }, ...l]);
      if (j.kind === "builder.ai_edit") {
        await files.reload();
        if (active) await open(active);
        if (preview) await startPreview();
      }
    },
    [files, active, open, preview], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const { job, running, track } = useJob(onJobDone);

  async function save() {
    if (!active || binary) return;
    try {
      await api.PUT("/api/v1/builder/projects/{project_id}/file", { params: { path: { project_id: id } }, body: { path: active, content } });
      setSaved(content);
      await files.reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function startPreview() {
    setPreviewBusy(true);
    setError(null);
    try {
      if (content !== saved) await save();
      const { data } = await api.POST("/api/v1/builder/projects/{project_id}/preview", { params: { path: { project_id: id } } });
      setPreview({ url: data!.url, nonce: Date.now() });
      setCenter("preview");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function runTests() {
    if (content !== saved) await save();
    const { data } = await api.POST("/api/v1/builder/projects/{project_id}/test", { params: { path: { project_id: id } } });
    await track(data!.job_id);
  }

  async function aiEdit() {
    if (!prompt.trim()) return;
    if (content !== saved) await save();
    setError(null);
    try {
      const { data } = await api.POST("/api/v1/builder/projects/{project_id}/ai-edit", { params: { path: { project_id: id } }, body: { prompt } });
      setPrompt("");
      await track(data!.job_id);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function newFile() {
    const path = window.prompt("New file path (e.g. src/utils.js)");
    if (!path) return;
    try {
      await api.PUT("/api/v1/builder/projects/{project_id}/file", { params: { path: { project_id: id } }, body: { path, content: "" } });
      await files.reload();
      await open(path);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function remove(f: WorkspaceFile) {
    if (!confirm(`Delete ${f.path}?`)) return;
    await api.DELETE("/api/v1/builder/projects/{project_id}/file", { params: { path: { project_id: id }, query: { path: f.path } } });
    if (active === f.path) setActive(null);
    await files.reload();
  }

  if (project.loading) return <Spinner />;
  const lastResult = log[0]?.job.result as (AiResult & { tests?: TestResult }) | null;

  return (
    <div className="-mx-4 -my-6 flex h-screen flex-col md:-mx-8 max-lg:h-[calc(100vh-53px)]">
      <header className="glass flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <h1 className="font-semibold">{project.data?.name}</h1>
        <Badge>{project.data?.builder_template}</Badge>
        <div className="ms-auto flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" icon={<FlaskConical className="size-4" />} onClick={runTests} disabled={running}>Run tests</Button>
          <Button size="sm" variant="secondary" icon={<Play className="size-4" />} onClick={startPreview} loading={previewBusy}>Preview</Button>
          {preview && (
            <Button size="sm" variant="ghost" icon={<Square className="size-4" />} onClick={async () => { await api.DELETE("/api/v1/builder/projects/{project_id}/preview", { params: { path: { project_id: id } } }); setPreview(null); setCenter("code"); }}>Stop</Button>
          )}
          <Button size="sm" variant="ghost" icon={<History className="size-4" />} onClick={() => setSnapshotsOpen(true)}>History</Button>
          <a href={`/api/v1/builder/projects/${id}/export`} className="inline-flex h-8 items-center gap-1 rounded-[10px] px-3 text-sm text-muted hover:bg-surface-2 hover:text-text"><Download className="size-4" /> ZIP</a>
        </div>
      </header>
      {error && <div className="px-4 pt-2"><Alert>{error}</Alert></div>}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[220px_1fr_340px]">
        {/* LEFT: files */}
        <aside className="min-h-0 overflow-y-auto border-e border-border bg-surface/40 p-2 max-lg:max-h-40">
          <div className="mb-1 flex items-center justify-between px-1 text-xs font-semibold uppercase text-muted">
            Files
            {!readOnly && <button onClick={newFile} aria-label="New file" className="hover:text-text"><FilePlus className="size-4" /></button>}
          </div>
          <ul>
            {tree.map((f) => (
              <li key={f.path} className="group flex items-center">
                <button onClick={() => open(f.path)} className={cx("flex min-w-0 flex-1 items-center gap-1.5 rounded-[8px] px-2 py-1 text-start font-mono text-xs", active === f.path ? "bg-primary/15 text-text" : "text-muted hover:bg-surface-2 hover:text-text")} style={{ paddingInlineStart: `${8 + (f.path.split("/").length - 1) * 10}px` }}>
                  <File className="size-3.5 shrink-0" />
                  <span className="truncate" dir="ltr">{f.path.split("/").pop()}</span>
                </button>
                {!readOnly && <button onClick={() => remove(f)} className="invisible p-1 text-muted hover:text-danger group-hover:visible" aria-label={`Delete ${f.path}`}><Trash2 className="size-3.5" /></button>}
              </li>
            ))}
          </ul>
        </aside>

        {/* CENTER: editor / preview */}
        <section className="flex min-h-0 flex-col">
          <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
            <Tabs<Center> value={center} onChange={setCenter} tabs={[{ id: "code", label: "Code" }, { id: "preview", label: "Live preview" }]} />
            {center === "code" && active && (
              <>
                <span className="truncate font-mono text-xs text-muted" dir="ltr">{active}{content !== saved ? " •" : ""}</span>
                {!readOnly && !binary && <Button size="sm" variant="ghost" icon={<Save className="size-4" />} onClick={save} disabled={content === saved}>Save</Button>}
              </>
            )}
            {center === "preview" && preview && (
              <>
                <Button size="sm" variant="ghost" icon={<RefreshCw className="size-4" />} onClick={() => setPreview({ ...preview, nonce: Date.now() })}>Reload</Button>
                <a href={preview.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent">Open <ExternalLink className="size-3" /></a>
              </>
            )}
          </div>
          <div className="min-h-[320px] flex-1 overflow-hidden">
            {center === "code" ? (
              binary ? <div className="p-4 text-sm text-muted">Binary file — download the project ZIP to inspect it.</div> : active ? <CodeEditor path={active} value={content} onChange={setContent} onSave={save} dark={theme === "dark"} /> : null
            ) : preview ? (
              // The preview is served from a separate origin; sandbox without allow-same-origin keeps it isolated from MIND.
              <iframe key={preview.nonce} src={preview.url} title="Live preview" className="h-full w-full bg-white" sandbox="allow-scripts allow-forms allow-modals allow-popups" />
            ) : (
              <div className="grid h-full place-items-center text-sm text-muted">Start the preview to run the app in an isolated container.</div>
            )}
          </div>
          {/* BOTTOM: terminal / tests */}
          <div className="max-h-56 min-h-24 overflow-y-auto border-t border-border bg-[#080a14] p-3 font-mono text-xs text-white/80" dir="ltr" aria-label="Terminal output">
            {lastResult?.tests ? (
              <>
                <div className={lastResult.tests.passed ? "text-green-400" : "text-red-400"}>
                  $ {lastResult.tests.command?.join(" ") ?? "tests"} — {lastResult.tests.ran ? (lastResult.tests.passed ? "PASSED" : "FAILED") : `not run: ${lastResult.tests.reason}`}
                  {lastResult.tests.duration_s != null && ` (${lastResult.tests.duration_s}s)`}
                </div>
                <pre className="whitespace-pre-wrap">{lastResult.tests.stdout}</pre>
                <pre className="whitespace-pre-wrap text-red-300">{lastResult.tests.stderr}</pre>
              </>
            ) : (
              <span className="text-white/40">Test and run output appears here.</span>
            )}
          </div>
        </section>

        {/* RIGHT: AI agent */}
        <aside className="flex min-h-0 flex-col border-s border-border bg-surface/40">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm font-semibold"><Bot className="size-4 text-primary" /> Coding agent</div>
          <div className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
            {job && running && <JobStatus job={job} />}
            {log.length === 0 && <p className="text-muted">Describe a change, e.g. “Add a scoreboard table with team names and points, saved in local storage.” The agent edits files, runs the tests in the sandbox and repairs a failing change once.</p>}
            {log.map((l, i) => {
              const r = l.job.result as AiResult | null;
              return (
                <div key={i} className="space-y-1 rounded-[10px] border border-border p-2">
                  <div className="flex items-center gap-2 text-xs">
                    <StatusBadge status={l.job.status} />
                    <span className="font-mono text-muted">{l.kind}</span>
                    <span className="ms-auto text-muted">{timeAgo(l.at)}</span>
                  </div>
                  {r?.summary && <p>{r.summary}</p>}
                  {r?.attempts?.map((a) => (
                    <div key={a.attempt} className="text-xs text-muted">
                      Attempt {a.attempt}{a.model ? ` (${a.model})` : ""}: {a.error ?? `${a.files?.length ?? 0} file(s) changed, tests ${a.tests_passed ? "passed" : a.tests_passed === false ? "failed" : "not run"}`}
                    </div>
                  ))}
                  {l.job.error != null && <p className="text-xs text-danger">{String((l.job.error as { message?: string }).message)}</p>}
                </div>
              );
            })}
          </div>
          {!readOnly && (
            <div className="border-t border-border p-3">
              <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the change you want…" onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void aiEdit(); }} />
              <Button className="mt-2 w-full" onClick={aiEdit} loading={running} disabled={!prompt.trim()}>Apply with AI</Button>
            </div>
          )}
        </aside>
      </div>
      <Snapshots id={id} open={snapshotsOpen} onClose={() => setSnapshotsOpen(false)} onRestored={async () => { await files.reload(); if (active) await open(active); }} />
    </div>
  );
}

function Snapshots({ id, open, onClose, onRestored }: { id: string; open: boolean; onClose: () => void; onRestored: () => void }) {
  const snaps = useLoad(async () => (open ? ((await api.GET("/api/v1/builder/projects/{project_id}/snapshots", { params: { path: { project_id: id } } })).data ?? []) : []), [id, open]);
  return (
    <Modal open={open} onClose={onClose} title="Workspace history">
      <p className="mb-3 text-sm text-muted">A snapshot is saved before every AI edit and restore. Restoring also snapshots the current state first.</p>
      <ul className="max-h-96 space-y-1 overflow-y-auto">
        {(snaps.data as { id: string; title: string; created_at: string }[] | undefined)?.map((s) => (
          <li key={s.id} className="flex items-center gap-2 rounded-[10px] px-2 py-1.5 text-sm hover:bg-surface-2">
            <span className="min-w-0 flex-1 truncate">{s.title}</span>
            <span className="text-xs text-muted">{timeAgo(s.created_at)}</span>
            <Button size="sm" variant="secondary" onClick={async () => { await api.POST("/api/v1/builder/projects/{project_id}/snapshots/{artifact_id}/restore", { params: { path: { project_id: id, artifact_id: s.id } } }); onRestored(); onClose(); }}>Restore</Button>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex justify-end">
        <Button variant="secondary" onClick={async () => { await api.POST("/api/v1/builder/projects/{project_id}/snapshots", { params: { path: { project_id: id }, query: { label: "manual snapshot" } } }); await snaps.reload(); }}>Save snapshot now</Button>
      </div>
    </Modal>
  );
}
