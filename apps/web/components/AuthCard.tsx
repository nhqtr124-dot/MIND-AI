"use client";

import type { ReactNode } from "react";
import { useI18n } from "@/lib/i18n";

export function AuthCard({ title, children, footer }: { title: string; children: ReactNode; footer: ReactNode }) {
  const { t, locale, setLocale } = useI18n();
  return (
    <div className="grid min-h-screen place-items-center p-4">
      <div className="w-full max-w-md animate-in">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 grid size-14 place-items-center rounded-[18px] bg-gradient-to-br from-primary to-accent text-2xl font-bold text-white shadow-[0_12px_40px_-10px_rgb(var(--mind-glow)/0.9)]">M</div>
          <h1 className="text-3xl font-semibold tracking-tight">{t("app.name")}</h1>
          <p className="mt-2 text-sm text-muted">{t("app.tagline")}</p>
        </div>
        <div className="rounded-[20px] border border-border bg-surface/90 p-6 shadow-2xl">
          <h2 className="mb-5 text-lg font-semibold">{title}</h2>
          {children}
        </div>
        <div className="mt-5 flex items-center justify-between text-sm text-muted">
          <div>{footer}</div>
          <button onClick={() => setLocale(locale === "en" ? "ar" : "en")} className="hover:text-text">
            {locale === "en" ? "العربية" : "English"}
          </button>
        </div>
      </div>
    </div>
  );
}
