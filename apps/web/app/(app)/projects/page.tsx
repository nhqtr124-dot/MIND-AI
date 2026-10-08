"use client";

import { FolderKanban, Lock, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Alert, Button, Card, Empty, Field, Input, Modal, PageHeader, Select, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

export default function ProjectsPage() {
  const org = useOrg();
  const { t } = useI18n();
  const { data, loading, reload } = useLoad(async () => (await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", visibility: "org" as "org" | "private" });
  const [error, setError] = useState<string | null>(null);
  const canCreate = org.role !== "viewer";

  return (
    <>
      <PageHeader title={t("nav.projects")} icon={<FolderKanban />} subtitle="Projects share files, artifacts, conversations and memory." actions={canCreate && <Button icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>{t("projects.new")}</Button>} />
      {loading ? (
        <Spinner />
      ) : data?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`}>
              <Card className="h-full transition hover:border-primary/60">
                <div className="flex items-center gap-2">
                  <h3 className="flex-1 truncate font-semibold">{p.name}</h3>
                  {p.visibility === "private" ? <Lock className="size-4 text-muted" /> : <Users className="size-4 text-muted" />}
                </div>
                <p className="mt-2 line-clamp-2 text-sm text-muted">{p.description || "—"}</p>
                <div className="mt-4 flex items-center justify-between text-xs text-muted">
                  <span>{p.role}</span>
                  <span>{timeAgo(p.updated_at)}</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <Empty icon={<FolderKanban className="size-8" />} title={t("common.empty")} />
      )}
      <Modal open={open} onClose={() => setOpen(false)} title={t("projects.new")}>
        <form
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api.POST("/api/v1/projects", { body: { org_id: org.id, ...form } });
              setOpen(false);
              setForm({ name: "", description: "", visibility: "org" });
              await reload();
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        >
          <Field label={t("projects.name")}>
            <Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t("projects.description")}>
            <Textarea rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
          <Field label={t("projects.visibility")}>
            <Select value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value as "org" | "private" })}>
              <option value="org">{t("projects.org")}</option>
              <option value="private">{t("projects.private")}</option>
            </Select>
          </Field>
          {error && <Alert>{error}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit">{t("common.create")}</Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
