"use client";

import { Shield } from "lucide-react";
import { useState } from "react";
import { Alert, Card, PageHeader, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Row = Record<string, string | number | null>;

function Table({ rows, cols }: { rows: Row[]; cols: [string, string][] }) {
  if (!rows.length) return <p className="text-sm text-muted">No usage in this period.</p>;
  return (
    <div className="overflow-x-auto"><table className="w-full text-sm">
      <thead><tr className="text-xs text-muted">{cols.map(([, l]) => <th key={l} className="py-1 text-start font-medium">{l}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-border">{cols.map(([k]) => <td key={k} className="py-1.5">{k === "cost_usd" ? `$${Number(r[k]).toFixed(4)}` : k === "day" ? String(r[k]).slice(0, 10) : String(r[k] ?? "—")}</td>)}</tr>)}</tbody>
    </table></div>
  );
}

export default function AdminPage() {
  const org = useOrg();
  const { t } = useI18n();
  const [days, setDays] = useState(30);
  const usage = useLoad(async () => (await api.GET("/api/v1/admin/usage", { params: { query: { org_id: org.id, days } } })).data, [org.id, days]);
  const audit = useLoad(async () => (await api.GET("/api/v1/admin/audit", { params: { query: { org_id: org.id, limit: 100 } } })).data ?? [], [org.id]);
  if (org.role !== "owner" && org.role !== "admin") return <Alert>Admins only.</Alert>;
  const u = usage.data;
  const counted: [string, string][] = [["events", "Calls"], ["input_tokens", "Input tok"], ["output_tokens", "Output tok"], ["cost_usd", "Cost"], ["unpriced_events", "Unpriced"]];
  return (
    <>
      <PageHeader icon={<Shield />} title={t("nav.admin")} subtitle="Usage and cost are recorded per call. Calls to models without configured prices are counted but not costed." actions={
        <Select className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}</Select>
      } />
      {!u ? <Spinner /> : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Card><div className="text-xs text-muted">Recorded cost</div><div className="mt-1 text-3xl font-semibold">${Number(u.total_cost_usd).toFixed(4)}</div></Card>
            <Card><div className="text-xs text-muted">Unpriced calls</div><div className="mt-1 text-3xl font-semibold">{u.unpriced_events}</div>{u.unpriced_events > 0 && <div className="mt-1 text-xs text-warning">Set model prices in Settings to cost these.</div>}</Card>
            <Card><div className="text-xs text-muted">Budget</div><div className="mt-1 text-xl font-semibold">{org.monthly_budget_usd ? `$${org.monthly_budget_usd} / month` : "No monthly cap"}</div></Card>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="By day"><Table rows={u.by_day as Row[]} cols={[["day", "Day"], ...counted]} /></Card>
            <Card title="By category"><Table rows={u.by_category as Row[]} cols={[["category", "Category"], ...counted]} /></Card>
            <Card title="By model"><Table rows={u.by_model as Row[]} cols={[["provider_kind", "Provider"], ["model_name", "Model"], ...counted]} /></Card>
            <Card title="By member"><Table rows={u.by_user as Row[]} cols={[["display_name", "Member"], ...counted]} /></Card>
          </div>
        </div>
      )}
      <Card className="mt-6" title="Audit log">
        <div className="max-h-[480px] overflow-y-auto"><table className="w-full text-sm">
          <tbody>{audit.data?.map((a) => (
            <tr key={a.id} className="border-t border-border align-top">
              <td className="py-1.5 pe-3 whitespace-nowrap text-xs text-muted">{new Date(a.created_at).toLocaleString()}</td>
              <td className="pe-3 font-mono text-xs">{a.action}</td>
              <td className="text-xs text-muted">{a.target_type ? `${a.target_type} ${a.target_id?.slice(0, 8)}` : ""} {Object.keys(a.details).length ? JSON.stringify(a.details) : ""}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>
    </>
  );
}
