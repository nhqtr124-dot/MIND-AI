"use client";

import {
  Bot,
  Box,
  Brain,
  Building2,
  FileText,
  FolderKanban,
  Hammer,
  Image as ImageIcon,
  Languages,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Mic,
  Moon,
  Search,
  Settings,
  Shield,
  Sun,
  Users,
  Video,
  Workflow,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { Badge, Select, Spinner, cx } from "./ui";

type NavItem = { href: string; key: MessageKey; icon: ReactNode; roadmap?: boolean; admin?: boolean };

const groups: { label: string; items: NavItem[] }[] = [
  {
    label: "Create",
    items: [
      { href: "/dashboard", key: "nav.dashboard", icon: <LayoutDashboard className="size-4" /> },
      { href: "/chat", key: "nav.chat", icon: <MessageSquare className="size-4" /> },
      { href: "/research", key: "nav.research", icon: <Search className="size-4" /> },
      { href: "/builder", key: "nav.builder", icon: <Hammer className="size-4" /> },
      { href: "/documents", key: "nav.documents", icon: <FileText className="size-4" /> },
      { href: "/3d", key: "nav.3d", icon: <Box className="size-4" /> },
      { href: "/image", key: "nav.image", icon: <ImageIcon className="size-4" /> },
      { href: "/video", key: "nav.video", icon: <Video className="size-4" />, roadmap: true },
      { href: "/voice", key: "nav.voice", icon: <Mic className="size-4" />, roadmap: true },
    ],
  },
  {
    label: "Operate",
    items: [
      { href: "/agents", key: "nav.agents", icon: <Bot className="size-4" /> },
      { href: "/automations", key: "nav.automations", icon: <Workflow className="size-4" /> },
      { href: "/projects", key: "nav.projects", icon: <FolderKanban className="size-4" /> },
      { href: "/memory", key: "nav.memory", icon: <Brain className="size-4" /> },
    ],
  },
  {
    label: "Organize",
    items: [
      { href: "/team", key: "nav.team", icon: <Users className="size-4" /> },
      { href: "/settings", key: "nav.settings", icon: <Settings className="size-4" /> },
      { href: "/admin", key: "nav.admin", icon: <Shield className="size-4" />, admin: true },
    ],
  },
];

export function Shell({ children }: { children: ReactNode }) {
  const { user, orgs, org, loading, setOrgId, logout } = useAuth();
  const { t, locale, setLocale } = useI18n();
  const { theme, toggle } = useTheme();
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!loading && !user) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [loading, user, router, path]);

  if (loading || !user || !org) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner className="size-6" />
      </div>
    );
  }
  const isAdmin = org.role === "owner" || org.role === "admin";

  const sidebar = (
    <nav className="flex h-full flex-col gap-4 overflow-y-auto p-4" aria-label="Main">
      <Link href="/dashboard" className="flex items-center gap-2.5 px-2 py-1">
        <span className="grid size-9 place-items-center rounded-[12px] bg-gradient-to-br from-primary to-accent text-sm font-bold text-white shadow-[0_8px_30px_-8px_rgb(var(--mind-glow)/0.9)]">M</span>
        <span className="text-lg font-semibold tracking-tight">{t("app.name")}</span>
      </Link>
      <label className="block px-1">
        <span className="sr-only">{t("common.workspace")}</span>
        <div className="flex items-center gap-2">
          <Building2 className="size-4 shrink-0 text-muted" />
          <Select value={org.id} onChange={(e) => setOrgId(e.target.value)} className="h-9">
            {orgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </div>
      </label>
      {groups.map((g) => (
        <div key={g.label}>
          <div className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-wider text-muted/80">{g.label}</div>
          <ul className="space-y-0.5">
            {g.items
              .filter((i) => !i.admin || isAdmin)
              .map((i) => {
                const active = path === i.href || path.startsWith(`${i.href}/`);
                return (
                  <li key={i.href}>
                    <Link
                      href={i.href}
                      aria-current={active ? "page" : undefined}
                      className={cx(
                        "flex items-center gap-2.5 rounded-[10px] px-2.5 py-2 text-sm transition",
                        active ? "bg-primary/15 text-text shadow-[inset_2px_0_0_var(--mind-primary)]" : "text-muted hover:bg-surface-2 hover:text-text",
                      )}
                    >
                      {i.icon}
                      <span className="flex-1">{t(i.key)}</span>
                      {i.roadmap && <Badge className="text-[10px]">{t("nav.roadmap")}</Badge>}
                    </Link>
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
      <div className="mt-auto space-y-2 border-t border-border pt-3">
        <div className="flex gap-1">
          <button onClick={toggle} className="flex flex-1 items-center justify-center gap-1.5 rounded-[10px] py-2 text-xs text-muted hover:bg-surface-2 hover:text-text" aria-label={t("common.theme")}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            {theme === "dark" ? t("common.light") : t("common.dark")}
          </button>
          <button onClick={() => setLocale(locale === "en" ? "ar" : "en")} className="flex flex-1 items-center justify-center gap-1.5 rounded-[10px] py-2 text-xs text-muted hover:bg-surface-2 hover:text-text" aria-label={t("common.language")}>
            <Languages className="size-4" /> {locale === "en" ? "العربية" : "English"}
          </button>
        </div>
        <div className="flex items-center gap-2 rounded-[12px] bg-surface-2/60 p-2">
          <span className="grid size-8 place-items-center rounded-full bg-primary/25 text-sm font-semibold">{user.display_name.slice(0, 1).toUpperCase()}</span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{user.display_name}</div>
            <div className="truncate text-xs text-muted">
              {org.role} · {user.email}
            </div>
          </div>
          <button onClick={logout} className="rounded-md p-1.5 text-muted hover:text-danger" aria-label={t("auth.signout")} title={t("auth.signout")}>
            <LogOut className="size-4" />
          </button>
        </div>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="glass sticky top-0 hidden h-screen w-64 shrink-0 border-e border-border lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-black/60" />
          <aside className="glass absolute inset-y-0 start-0 w-72 border-e border-border" onClick={(e) => e.stopPropagation()}>
            <button className="absolute end-3 top-4 text-muted" onClick={() => setOpen(false)} aria-label="Close menu">
              <X className="size-5" />
            </button>
            {sidebar}
          </aside>
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="glass sticky top-0 z-30 flex items-center gap-3 border-b border-border px-4 py-3 lg:hidden">
          <button onClick={() => setOpen(true)} aria-label="Open menu" className="text-muted">
            <Menu className="size-5" />
          </button>
          <span className="font-semibold">{t("app.name")}</span>
        </div>
        <main className="mx-auto w-full max-w-[1400px] px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
