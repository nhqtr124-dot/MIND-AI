"use client";

import { Hammer, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Card, Empty, Field, Input, Modal, PageHeader, Select, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Tpl = { key: string; title: string; description: string; runtime: string; files: string[] };

export default function BuilderHome() {
  const org = useOrg();
  const router = useRouter();
  const { t } = useI18n();
  const projects = useLoad(async () => ((await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? []).filter((p) => p.builder_template), [org.id]);
  const templates = useLoad(async () => (await api.GET("/api/v1/builder/templates")).data as unknown as Tpl[], []);
  const caps = useLoad(async () => (await api.GET("/api/v1/capabilities", { params: { query: { org_id: org.id } } })).data as { features: Record<string, { status: string; detail: string; ai_edits?: boolean }> }, [org.id]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("static-web");
  const [error, setError] = useState<string | null>(null);
  const builder = caps.data?.features.builder;

  return (
    <>
      <PageHeader icon={<Hammer />} title={t("nav.builder")} subtitle="Real projects: files, sandboxed tests, live previews, AI edits and ZIP export." actions={<Button icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>New app</Button>} />
      {builder && builder.status !== "available" && <div className="mb-4"><Alert tone="warning">{builder.detail}</Alert></div>}
      {builder?.status === "available" && builder.ai_edits === false && <div className="mb-4"><Alert tone="accent">AI edits need an enabled chat model. You can still edit code, run tests and preview.</Alert></div>}
      {projects.loading ? (
        <Spinner />
      ) : projects.data?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data.map((p) => (
            <Link key={p.id} href={`/builder/${p.id}`}>
              <Card className="h-full transition hover:border-primary/60">
                <h3 className="font-semibold">{p.name}</h3>
                <p className="mt-1 text-sm text-muted">{p.builder_template}</p>
                <p className="mt-3 text-xs text-muted">{timeAgo(p.updated_at)}</p>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <Empty icon={<Hammer className="size-8" />} title="No apps yet">Start from a runnable template, then describe changes in plain language.</Empty>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="New app">
        <form
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const { data } = await api.POST("/api/v1/projects", { body: { org_id: org.id, name, builder_template: template } });
              router.push(`/builder/${data!.id}`);
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        >
          <Field label="Name"><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Robotics competition dashboard" /></Field>
          <Field label="Starting point" hint={templates.data?.find((x) => x.key === template)?.description}>
            <Select value={template} onChange={(e) => setTemplate(e.target.value)}>
              {templates.data?.map((x) => <option key={x.key} value={x.key}>{x.title} ({x.runtime})</option>)}
            </Select>
          </Field>
          {error && <Alert>{error}</Alert>}
          <div className="flex justify-end"><Button type="submit">{t("common.create")}</Button></div>
        </form>
      </Modal>
    </>
  );
}
