"use client";

import type { Artifact } from "@mind/shared-types";
import { CheckCircle2, CircleAlert, CircleDashed, Download, Link2, XCircle } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { api, errorMessage, mind } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatBytes } from "@/lib/hooks";
import { Markdown } from "./Markdown";
import { Alert, Badge, Button, Card, Select, StatusBadge } from "./ui";

const StlViewer = dynamic(() => import("./StlViewer").then((m) => m.StlViewer), { ssr: false, loading: () => <div className="h-[420px] rounded-[14px] border border-border" /> });

type Check = { id: string; label?: string; status: string; detail: string; critical?: boolean };

function CheckIcon({ status }: { status: string }) {
  if (status === "pass") return <CheckCircle2 className="size-4 shrink-0 text-success" />;
  if (status === "fail") return <XCircle className="size-4 shrink-0 text-danger" />;
  if (status === "warn") return <CircleAlert className="size-4 shrink-0 text-warning" />;
  return <CircleDashed className="size-4 shrink-0 text-muted" />;
}

export function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="divide-y divide-border rounded-[12px] border border-border">
      {checks.map((c, i) => (
        <li key={`${c.id}-${i}`} className="flex items-start gap-2 px-3 py-2 text-sm">
          <CheckIcon status={c.status} />
          <div className="min-w-0">
            <div className="font-medium">{c.label ?? c.id.replaceAll("_", " ")}</div>
            <div className="text-xs break-words text-muted">{c.detail}</div>
          </div>
          <span className="ms-auto text-xs text-muted">{c.status.replace("_", " ")}</span>
        </li>
      ))}
    </ul>
  );
}

type Validation = Record<string, unknown> & { checks?: Check[]; parts?: { name: string; validation: { status: string; checks: Check[]; metrics: Record<string, unknown>; disclaimer: string } }[] };

function ValidationPanel({ kind, validation }: { kind: string; validation: Validation | null | undefined }) {
  if (!validation) return null;
  if (kind === "cad" && validation.parts) {
    const bom = (validation.bom as { item: string; type: string; quantity: number; volume_cm3?: number; est_mass_g_pla_solid?: number; note?: string }[]) ?? [];
    return (
      <div className="space-y-4">
        {validation.parts.map((p) => (
          <div key={p.name} className="space-y-2">
            <div className="flex items-center gap-2">
              <h4 className="font-medium">{p.name}</h4>
              <StatusBadge status={p.validation.status} />
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-muted">
              {Object.entries(p.validation.metrics).map(([k, v]) => (
                <span key={k} className="rounded-md bg-surface-2 px-2 py-0.5">
                  {k.replaceAll("_", " ")}: {Array.isArray(v) ? v.join(" × ") : String(v)}
                </span>
              ))}
            </div>
            <Checks checks={p.validation.checks} />
          </div>
        ))}
        <p className="text-xs text-warning">{validation.parts[0]?.validation.disclaimer}</p>
        {bom.length > 0 && (
          <div>
            <h4 className="mb-2 font-medium">Bill of materials</h4>
            <table className="w-full text-sm">
              <thead className="text-start text-xs text-muted">
                <tr>
                  <th className="text-start">Item</th>
                  <th className="text-start">Type</th>
                  <th className="text-start">Qty</th>
                  <th className="text-start">Volume / mass</th>
                </tr>
              </thead>
              <tbody>
                {bom.map((b, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1">{b.item}</td>
                    <td>{b.type}</td>
                    <td>{b.quantity}</td>
                    <td className="text-muted">{b.volume_cm3 != null ? `${b.volume_cm3} cm³ · ~${b.est_mass_g_pla_solid} g PLA (solid)` : (b.note ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {Array.isArray(validation.assumptions) && (
          <div>
            <h4 className="mb-1 font-medium">Assumptions</h4>
            <ul className="list-disc space-y-0.5 ps-5 text-sm text-muted">
              {(validation.assumptions as string[]).map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }
  if (validation.checks) return <Checks checks={validation.checks} />;
  return <pre className="max-h-64 overflow-auto rounded-[10px] bg-surface-2 p-3 text-xs">{JSON.stringify(validation, null, 2)}</pre>;
}

export function ArtifactView({ artifact, onChange }: { artifact: Artifact; onChange?: (a: Artifact) => void }) {
  const { org } = useAuth();
  const [version, setVersion] = useState(artifact.current_version);
  const [report, setReport] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => setVersion(artifact.current_version), [artifact.id, artifact.current_version]);
  const versions = artifact.versions ?? [];
  const v = versions.find((x) => x.version === version);
  const files = v?.files ?? [];
  const stl = files.find((f) => f.format === "stl");
  const md = files.find((f) => f.format === "md");
  const images = files.filter((f) => f.mime_type.startsWith("image/"));

  useEffect(() => {
    setReport(null);
    if (md) fetch(mind.artifactFileUrl(artifact.id, md.name, version), { credentials: "include" }).then(async (r) => r.ok && setReport(await r.text()));
  }, [artifact.id, md?.name, version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (org) api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } }).then(({ data }) => setProjects(data ?? []));
  }, [org]);

  async function share(name: string) {
    try {
      const url = await mind.signedUrl(artifact.id, name, version);
      await navigator.clipboard.writeText(url).catch(() => undefined);
      setMsg(`Signed download link (valid 1 hour) copied: ${url}`);
    } catch (e) {
      setMsg(errorMessage(e));
    }
  }

  async function move(projectId: string) {
    const { data } = await api.PATCH("/api/v1/artifacts/{artifact_id}", { params: { path: { artifact_id: artifact.id } }, body: { project_id: projectId || null } });
    if (data) onChange?.(data);
  }

  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {artifact.title} <StatusBadge status={artifact.status} />
          {artifact.validation_status && <StatusBadge status={artifact.validation_status} />}
        </span>
      }
      actions={
        versions.length > 1 && (
          <Select value={version} onChange={(e) => setVersion(Number(e.target.value))} className="h-8 w-28">
            {versions.map((x) => (
              <option key={x.version} value={x.version}>
                v{x.version}
              </option>
            ))}
          </Select>
        )
      }
    >
      {versions.length === 0 && <Alert tone="warning">No files were produced{artifact.status === "failed" ? " — the job failed. See the job error for details." : " yet."}</Alert>}
      {stl && <StlViewer url={mind.artifactFileUrl(artifact.id, stl.name, version)} />}
      {images.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {images.map((im) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={im.name} src={mind.artifactFileUrl(artifact.id, im.name, version, true)} alt={im.name} className="w-full rounded-[12px] border border-border bg-white object-contain" />
          ))}
        </div>
      )}
      {report && (
        <div className="mt-4 max-h-[600px] overflow-auto rounded-[12px] border border-border p-4">
          <Markdown>{report}</Markdown>
        </div>
      )}
      {files.length > 0 && (
        <div className="mt-4 space-y-2">
          <h3 className="text-sm font-semibold">Files</h3>
          <ul className="divide-y divide-border rounded-[12px] border border-border">
            {files.map((f) => (
              <li key={f.name} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                <Badge tone="primary">{f.format ?? "file"}</Badge>
                <span className="min-w-0 truncate">{f.name}</span>
                <span className="text-xs text-muted">{formatBytes(f.size_bytes)}</span>
                <span className="hidden font-mono text-[10px] text-muted md:inline" title="SHA-256">
                  {f.sha256.slice(0, 12)}…
                </span>
                <span className="ms-auto flex gap-1">
                  <a href={mind.artifactFileUrl(artifact.id, f.name, version)} className="inline-flex h-8 items-center gap-1 rounded-[8px] px-2 text-muted hover:bg-surface-2 hover:text-text" download>
                    <Download className="size-4" /> Download
                  </a>
                  <Button variant="ghost" size="sm" onClick={() => share(f.name)} icon={<Link2 className="size-4" />} aria-label="Copy signed link" />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {msg && (
        <div className="mt-3">
          <Alert tone="accent">{msg}</Alert>
        </div>
      )}
      {v?.validation && (
        <details className="mt-4" open={artifact.kind === "cad"}>
          <summary className="cursor-pointer text-sm font-semibold">Validation report</summary>
          <div className="mt-3">
            <ValidationPanel kind={artifact.kind} validation={v.validation as Validation} />
          </div>
        </details>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-muted">
        <span>Project:</span>
        <Select value={artifact.project_id ?? ""} onChange={(e) => move(e.target.value)} className="h-8 w-56">
          <option value="">No project</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
      </div>
    </Card>
  );
}
