"use client";

import { ArrowRight, Box, FileText, FolderKanban, Hammer, MessageSquare, Search } from "lucide-react";
import Link from "next/link";
import { Card, Empty, PageHeader, Spinner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useOrg, useAuth } from "@/lib/auth";
import { timeAgo, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Feature = { status: string; detail: string };

const quick = [
  { href: "/chat", icon: MessageSquare, title: "Ask MIND", text: "Chat with configured models, attach files." },
  { href: "/3d", icon: Box, title: "Design a part", text: "Parametric STL/STEP with validation." },
  { href: "/documents", icon: FileText, title: "Create a document", text: "DOCX, PDF, PPTX, XLSX — verified." },
  { href: "/builder", icon: Hammer, title: "Build an app", text: "Sandboxed code, tests and live preview." },
  { href: "/research", icon: Search, title: "Research", text: "Sources, evidence and checked citations." },
];

export default function Dashboard() {
  const org = useOrg();
  const { user } = useAuth();
  const { t } = useI18n();
  const projects = useLoad(async () => (await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const jobs = useLoad(async () => (await api.GET("/api/v1/jobs", { params: { query: { org_id: org.id, limit: 8 } } })).data ?? [], [org.id]);
  const caps = useLoad(async () => (await api.GET("/api/v1/capabilities", { params: { query: { org_id: org.id } } })).data as { features: Record<string, Feature>; server: Record<string, unknown> }, [org.id]);

  return (
    <>
      <PageHeader title={`${t("dashboard.welcome")}, ${user?.display_name}`} subtitle={org.name} />
      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {quick.map((q) => (
          <Link key={q.href} href={q.href} className="group rounded-[16px] border border-border bg-surface/70 p-4 transition hover:-translate-y-0.5 hover:border-primary/60">
            <q.icon className="mb-3 size-5 text-primary" />
            <div className="font-medium">{q.title}</div>
            <div className="mt-1 text-xs text-muted">{q.text}</div>
            <ArrowRight className="mt-3 size-4 text-muted transition group-hover:translate-x-1 group-hover:text-primary rtl:rotate-180" />
          </Link>
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title={t("dashboard.projects")} actions={<Link href="/projects" className="text-sm text-accent">All</Link>}>
          {projects.loading ? (
            <Spinner />
          ) : projects.data?.length ? (
            <ul className="space-y-1">
              {projects.data.slice(0, 6).map((p) => (
                <li key={p.id}>
                  <Link href={`/projects/${p.id}`} className="flex items-center gap-2 rounded-[10px] px-2 py-2 hover:bg-surface-2">
                    <FolderKanban className="size-4 text-muted" />
                    <span className="flex-1 truncate">{p.name}</span>
                    <span className="text-xs text-muted">{timeAgo(p.updated_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title={t("common.empty")}>
              <Link href="/projects" className="text-accent">{t("projects.new")}</Link>
            </Empty>
          )}
        </Card>
        <Card title={t("dashboard.jobs")}>
          {jobs.data?.length ? (
            <ul className="space-y-2 text-sm">
              {jobs.data.map((j) => (
                <li key={j.id} className="flex items-center gap-2">
                  <StatusBadge status={j.status} />
                  <span className="font-mono text-xs">{j.kind}</span>
                  <span className="ms-auto text-xs text-muted">{timeAgo(j.created_at)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">{jobs.loading ? t("common.loading") : t("common.empty")}</p>
          )}
        </Card>
        <Card title={t("dashboard.capabilities")}>
          {caps.data ? (
            <ul className="space-y-2 text-sm">
              {Object.entries(caps.data.features).map(([k, f]) => (
                <li key={k} className="flex items-start gap-2" title={f.detail}>
                  <span className="w-32 shrink-0 capitalize">{k.replaceAll("_", " ")}</span>
                  <StatusBadge status={f.status} />
                </li>
              ))}
            </ul>
          ) : (
            <Spinner />
          )}
        </Card>
      </div>
    </>
  );
}
