"use client";

import { Copy, Plus, Users } from "lucide-react";
import { useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth, useOrg } from "@/lib/auth";
import { useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Role = "owner" | "admin" | "editor" | "viewer";

export default function TeamPage() {
  const org = useOrg();
  const { user, reload, setOrgId } = useAuth();
  const { t } = useI18n();
  const isAdmin = org.role === "owner" || org.role === "admin";
  const members = useLoad(async () => (await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } })).data ?? [], [org.id]);
  const invites = useLoad(async () => (isAdmin ? ((await api.GET("/api/v1/orgs/{org_id}/invitations", { params: { path: { org_id: org.id } } })).data ?? []) : []), [org.id, isAdmin]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "editor" | "viewer">("editor");
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newOrg, setNewOrg] = useState(false);
  const [orgName, setOrgName] = useState("");

  return (
    <>
      <PageHeader icon={<Users />} title={t("nav.team")} subtitle={`${org.name} · your role: ${org.role}`} actions={<Button variant="secondary" icon={<Plus className="size-4" />} onClick={() => setNewOrg(true)}>New organization</Button>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <Card title={t("team.members")}>
          <ul className="divide-y divide-border">
            {members.data?.map((m) => (
              <li key={m.user_id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                <span className="grid size-8 place-items-center rounded-full bg-primary/20 font-semibold">{m.display_name[0]?.toUpperCase()}</span>
                <div className="min-w-0 flex-1"><div className="font-medium">{m.display_name}{m.user_id === user?.id && " (you)"}</div><div className="text-xs text-muted">{m.email}</div></div>
                {isAdmin && m.user_id !== user?.id ? (
                  <Select className="h-8 w-28" value={m.role} onChange={async (e) => {
                    try { await api.PATCH("/api/v1/orgs/{org_id}/members/{member_id}", { params: { path: { org_id: org.id, member_id: m.user_id } }, body: { role: e.target.value as Role } }); await members.reload(); } catch (err) { setError(errorMessage(err)); }
                  }}>{(["owner", "admin", "editor", "viewer"] as Role[]).map((r) => <option key={r} value={r} disabled={r === "owner" && org.role !== "owner"}>{r}</option>)}</Select>
                ) : <Badge>{m.role}</Badge>}
                {(isAdmin || m.user_id === user?.id) && !(org.is_personal && m.user_id === user?.id) && (
                  <Button size="sm" variant="ghost" onClick={async () => {
                    if (!confirm(m.user_id === user?.id ? "Leave this organization?" : `Remove ${m.display_name}?`)) return;
                    try { await api.DELETE("/api/v1/orgs/{org_id}/members/{member_id}", { params: { path: { org_id: org.id, member_id: m.user_id } } }); if (m.user_id === user?.id) await reload(); else await members.reload(); } catch (err) { setError(errorMessage(err)); }
                  }}>{m.user_id === user?.id ? "Leave" : "Remove"}</Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
        {isAdmin && (
          <Card title={t("team.invite")}>
            <div className="space-y-3">
              <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
              <Field label="Role"><Select value={role} onChange={(e) => setRole(e.target.value as "editor")}><option value="viewer">viewer — read only</option><option value="editor">editor — create and edit</option><option value="admin">admin — manage members & providers</option></Select></Field>
              <Button className="w-full" disabled={!email} onClick={async () => {
                try { const { data } = await api.POST("/api/v1/orgs/{org_id}/invitations", { params: { path: { org_id: org.id } }, body: { email, role } }); setLink(data!.accept_url ?? null); setEmail(""); await invites.reload(); } catch (e) { setError(errorMessage(e)); }
              }}>Create invitation</Button>
              {link && (
                <Alert tone="accent">
                  <p className="mb-1">No email service is configured, so share this one-time link with the invitee (valid 7 days):</p>
                  <div className="flex items-center gap-2"><code className="min-w-0 flex-1 truncate text-xs">{link}</code><button aria-label="Copy link" onClick={() => navigator.clipboard.writeText(link)}><Copy className="size-4" /></button></div>
                </Alert>
              )}
              <ul className="space-y-1 text-sm">
                {invites.data?.filter((i) => !i.accepted_at && !i.revoked_at).map((i) => (
                  <li key={i.id} className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate">{i.email}</span><Badge>{i.role}</Badge>
                    <Button size="sm" variant="ghost" onClick={async () => { await api.DELETE("/api/v1/orgs/{org_id}/invitations/{invitation_id}", { params: { path: { org_id: org.id, invitation_id: i.id } } }); await invites.reload(); }}>Revoke</Button></li>
                ))}
              </ul>
            </div>
          </Card>
        )}
      </div>
      <Modal open={newOrg} onClose={() => setNewOrg(false)} title="New organization">
        <div className="space-y-3">
          <Field label="Name"><Input value={orgName} onChange={(e) => setOrgName(e.target.value)} /></Field>
          <Button disabled={!orgName.trim()} onClick={async () => { const { data } = await api.POST("/api/v1/orgs", { body: { name: orgName } }); await reload(); setOrgId(data!.id); setNewOrg(false); }}>{t("common.create")}</Button>
        </div>
      </Modal>
    </>
  );
}
