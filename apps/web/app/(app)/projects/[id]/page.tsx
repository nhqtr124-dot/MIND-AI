"use client";

import type { Artifact } from "@mind/shared-types";
import { FileUp, FolderKanban, Hammer, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ArtifactView } from "@/components/ArtifactView";
import { Alert, Badge, Button, Card, Empty, Field, Input, PageHeader, Select, Spinner, StatusBadge, Tabs, Textarea } from "@/components/ui";
import { api, errorMessage, mind } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { formatBytes, timeAgo, useLoad } from "@/lib/hooks";

type Tab = "files" | "artifacts" | "sharing" | "settings";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const org = useOrg();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("artifacts");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Artifact | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const project = useLoad(async () => (await api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: id } } })).data!, [id]);
  const files = useLoad(async () => (await api.GET("/api/v1/files", { params: { query: { project_id: id } } })).data ?? [], [id]);
  const artifacts = useLoad(async () => (await api.GET("/api/v1/artifacts", { params: { query: { project_id: id } } })).data ?? [], [id]);
  const members = useLoad(async () => (await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })).data ?? [], [org.id]);
  const shares = useLoad(async () => (await api.GET("/api/v1/projects/{project_id}/members", { params: { path: { project_id: id } } })).data ?? [], [id]);
  const [edit, setEdit] = useState<{ name: string; description: string } | null>(null);

  if (project.loading) return <Spinner />;
  if (!project.data) return <Alert>{project.error ?? "Project not found"}</Alert>;
  const p = project.data;
  const canEdit = p.role !== "viewer";

  async function upload(list: FileList | null) {
    if (!list) return;
    setError(null);
    for (const f of Array.from(list)) {
      try {
        await mind.upload(f, f.name, { project_id: id });
      } catch (e) {
        setError(`${f.name}: ${errorMessage(e)}`);
      }
    }
    await files.reload();
  }

  return (
    <>
      <PageHeader
        icon={<FolderKanban />}
        title={p.name}
        subtitle={
          <span className="flex items-center gap-2">
            <Badge>{p.visibility === "private" ? "private" : "organization"}</Badge> your role: {p.role}
          </span>
        }
        actions={
          p.builder_template && (
            <Link href={`/builder/${p.id}`}>
              <Button icon={<Hammer className="size-4" />}>Open in Builder</Button>
            </Link>
          )
        }
      />
      <div className="mb-4">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "artifacts", label: `Artifacts (${artifacts.data?.length ?? 0})` },
            { id: "files", label: `Files (${files.data?.length ?? 0})` },
            { id: "sharing", label: "Sharing" },
            { id: "settings", label: "Settings" },
          ]}
        />
      </div>
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}

      {tab === "files" && (
        <Card
          title="Uploaded files"
          actions={
            canEdit && (
              <>
                <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
                <Button size="sm" icon={<FileUp className="size-4" />} onClick={() => fileInput.current?.click()}>Upload</Button>
              </>
            )
          }
        >
          {files.data?.length ? (
            <ul className="divide-y divide-border">
              {files.data.map((f) => (
                <li key={f.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                  <span className="min-w-0 flex-1 truncate">{f.path}</span>
                  {f.has_text ? <Badge tone="success">text extracted</Badge> : f.extraction_error ? <Badge tone="warning" >{f.extraction_error.slice(0, 40)}</Badge> : null}
                  <span className="text-xs text-muted">{formatBytes(f.size_bytes)} · {timeAgo(f.created_at)}</span>
                  <a className="text-accent" href={`/api/v1/files/${f.id}/download`}>Download</a>
                  {canEdit && (
                    <button
                      className="text-muted hover:text-danger"
                      aria-label={`Delete ${f.path}`}
                      onClick={async () => {
                        await api.DELETE("/api/v1/files/{file_id}", { params: { path: { file_id: f.id } } });
                        await files.reload();
                      }}
                    >
                      <Trash2 className="size-4" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No files">Upload PDFs, Office files, images or meshes to use them in chat, documents and CAD.</Empty>
          )}
        </Card>
      )}

      {tab === "artifacts" && (
        <div className="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
          <Card title="Generated artifacts">
            {artifacts.data?.length ? (
              <ul className="space-y-1">
                {artifacts.data.map((a) => (
                  <li key={a.id}>
                    <button onClick={() => setSelected(a)} className={`w-full rounded-[10px] px-2 py-2 text-start hover:bg-surface-2 ${selected?.id === a.id ? "bg-surface-2" : ""}`}>
                      <div className="flex items-center gap-2">
                        <Badge tone="primary">{a.kind}</Badge>
                        <span className="min-w-0 flex-1 truncate text-sm">{a.title}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                        <StatusBadge status={a.validation_status ?? a.status} /> v{a.current_version} · {timeAgo(a.created_at)}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No artifacts yet">Generate CAD parts, documents, images or research reports into this project.</Empty>
            )}
          </Card>
          {selected ? <ArtifactView artifact={selected} onChange={(a) => { setSelected(a); void artifacts.reload(); }} /> : <Empty title="Select an artifact" />}
        </div>
      )}

      {tab === "sharing" && (
        <Card title="Who can access this project">
          {p.visibility === "org" ? (
            <p className="text-sm text-muted">Everyone in {org.name} can access this project according to their organization role.</p>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted">Private: only you, people you share with, and organization admins/owners.</p>
              <ul className="divide-y divide-border text-sm">
                {shares.data?.map((s) => {
                  const m = members.data?.find((x) => x.user_id === s.user_id);
                  return (
                    <li key={s.user_id} className="flex items-center gap-2 py-2">
                      <span className="flex-1">{m?.display_name ?? s.user_id}</span>
                      <Badge>{s.role}</Badge>
                      {canEdit && (
                        <Button size="sm" variant="ghost" onClick={async () => { await api.DELETE("/api/v1/projects/{project_id}/members/{member_id}", { params: { path: { project_id: id, member_id: s.user_id } } }); await shares.reload(); }}>
                          Remove
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
              {canEdit && (
                <form
                  className="flex flex-wrap gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const fd = new FormData(e.currentTarget);
                    try {
                      await api.POST("/api/v1/projects/{project_id}/members", { params: { path: { project_id: id } }, body: { user_id: String(fd.get("user")), role: fd.get("role") as "editor" | "viewer" } });
                      await shares.reload();
                    } catch (err) {
                      setError(errorMessage(err));
                    }
                  }}
                >
                  <Select name="user" className="w-60">
                    {members.data?.map((m) => (
                      <option key={m.user_id} value={m.user_id}>{m.display_name} ({m.email})</option>
                    ))}
                  </Select>
                  <Select name="role" className="w-32">
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                  </Select>
                  <Button type="submit">Share</Button>
                </form>
              )}
            </div>
          )}
        </Card>
      )}

      {tab === "settings" && canEdit && (
        <Card title="Project settings">
          <div className="max-w-xl space-y-4">
            <Field label="Name">
              <Input value={edit?.name ?? p.name} onChange={(e) => setEdit({ name: e.target.value, description: edit?.description ?? p.description })} />
            </Field>
            <Field label="Description">
              <Textarea rows={3} value={edit?.description ?? p.description} onChange={(e) => setEdit({ name: edit?.name ?? p.name, description: e.target.value })} />
            </Field>
            <Field label="Visibility">
              <Select value={p.visibility} onChange={async (e) => { await api.PATCH("/api/v1/projects/{project_id}", { params: { path: { project_id: id } }, body: { visibility: e.target.value as "org" | "private" } }).catch((err) => setError(errorMessage(err))); await project.reload(); }}>
                <option value="org">Whole organization</option>
                <option value="private">Private</option>
              </Select>
            </Field>
            <div className="flex gap-2">
              <Button disabled={!edit} onClick={async () => { await api.PATCH("/api/v1/projects/{project_id}", { params: { path: { project_id: id } }, body: edit! }); setEdit(null); await project.reload(); }}>Save</Button>
              <Button variant="danger" onClick={async () => { if (confirm("Archive this project? It will disappear from lists.")) { await api.DELETE("/api/v1/projects/{project_id}", { params: { path: { project_id: id } } }); router.push("/projects"); } }}>
                Archive project
              </Button>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}
