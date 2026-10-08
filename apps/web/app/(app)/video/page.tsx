"use client";

import { Video } from "lucide-react";
import { Alert, Card, PageHeader, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

/** Honest status page: this studio is not implemented yet, and the page says so. */
export default function Page() {
  const org = useOrg();
  const { t } = useI18n();
  const caps = useLoad(async () => (await api.GET("/api/v1/capabilities", { params: { query: { org_id: org.id } } })).data as { features: Record<string, { status: string; detail: string }> }, [org.id]);
  const f = caps.data?.features["video"];
  return (
    <>
      <PageHeader icon={<Video />} title={t("nav.video")} subtitle={f && <StatusBadge status={f.status} />} />
      <Card>
        <Alert tone="warning">{f?.detail ?? "Not implemented yet."}</Alert>
        <p className="mt-4 text-sm text-muted">Planned: storyboard and script generation, provider-based text/image-to-video, FFmpeg timeline editing (trim, transitions, captions, voiceover), and playable export with container, duration and stream validation. Nothing on this page generates footage today.</p>
      </Card>
    </>
  );
}
