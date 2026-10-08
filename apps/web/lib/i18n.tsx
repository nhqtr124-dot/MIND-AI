"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Locale = "en" | "ar";

const en = {
  "app.name": "MIND AI",
  "app.tagline": "One workspace for chat, research, code, documents and 3D.",
  "nav.dashboard": "Dashboard",
  "nav.chat": "MIND Chat",
  "nav.research": "MIND Research",
  "nav.builder": "MIND Builder",
  "nav.image": "MIND Image",
  "nav.video": "MIND Video",
  "nav.documents": "MIND Documents",
  "nav.voice": "MIND Voice",
  "nav.3d": "MIND 3D",
  "nav.agents": "MIND Agents",
  "nav.automations": "MIND Automations",
  "nav.projects": "MIND Projects",
  "nav.memory": "MIND Memory",
  "nav.team": "MIND Team",
  "nav.settings": "MIND Settings",
  "nav.admin": "MIND Admin",
  "nav.roadmap": "Roadmap",
  "auth.signin": "Sign in",
  "auth.signup": "Create account",
  "auth.signout": "Sign out",
  "auth.email": "Email",
  "auth.password": "Password",
  "auth.name": "Display name",
  "auth.noAccount": "No account yet?",
  "auth.haveAccount": "Already have an account?",
  "auth.passwordHint": "At least 10 characters.",
  "common.save": "Save",
  "common.cancel": "Cancel",
  "common.create": "Create",
  "common.delete": "Delete",
  "common.download": "Download",
  "common.loading": "Loading…",
  "common.search": "Search",
  "common.new": "New",
  "common.send": "Send",
  "common.stop": "Stop",
  "common.retry": "Retry",
  "common.status": "Status",
  "common.empty": "Nothing here yet.",
  "common.project": "Project",
  "common.noProject": "No project",
  "common.workspace": "Workspace",
  "common.language": "Language",
  "common.theme": "Theme",
  "common.dark": "Dark",
  "common.light": "Light",
  "chat.placeholder": "Message MIND AI…",
  "chat.new": "New chat",
  "chat.auto": "Auto",
  "chat.manual": "Manual",
  "chat.attach": "Attach files",
  "chat.regenerate": "Regenerate",
  "chat.edit": "Edit",
  "chat.export": "Export",
  "chat.noModel": "No chat model is enabled yet. An admin can add a provider in Settings.",
  "chat.empty": "Ask anything. Attach documents or images to discuss them.",
  "dashboard.welcome": "Welcome back",
  "dashboard.projects": "Recent projects",
  "dashboard.jobs": "Recent jobs",
  "dashboard.capabilities": "Feature availability",
  "projects.new": "New project",
  "projects.name": "Project name",
  "projects.description": "Description",
  "projects.visibility": "Visibility",
  "projects.private": "Private",
  "projects.org": "Whole organization",
  "cad.title": "Parametric 3D parts",
  "cad.generate": "Generate part",
  "cad.describe": "Describe the part",
  "docs.title": "Document studio",
  "docs.generate": "Generate file",
  "team.members": "Members",
  "team.invite": "Invite member",
  "settings.providers": "AI providers",
  "settings.models": "Models",
  "settings.budgets": "Budgets & policy",
  "settings.integrations": "Integrations",
} as const;

export type MessageKey = keyof typeof en;

const ar: Record<MessageKey, string> = {
  "app.name": "مايند AI",
  "app.tagline": "مساحة عمل واحدة للمحادثة والبحث والبرمجة والمستندات والتصميم ثلاثي الأبعاد.",
  "nav.dashboard": "لوحة التحكم",
  "nav.chat": "محادثة مايند",
  "nav.research": "بحث مايند",
  "nav.builder": "منشئ التطبيقات",
  "nav.image": "استوديو الصور",
  "nav.video": "استوديو الفيديو",
  "nav.documents": "المستندات",
  "nav.voice": "الصوت",
  "nav.3d": "التصميم ثلاثي الأبعاد",
  "nav.agents": "الوكلاء",
  "nav.automations": "الأتمتة",
  "nav.projects": "المشاريع",
  "nav.memory": "الذاكرة",
  "nav.team": "الفريق",
  "nav.settings": "الإعدادات",
  "nav.admin": "الإدارة",
  "nav.roadmap": "قيد التطوير",
  "auth.signin": "تسجيل الدخول",
  "auth.signup": "إنشاء حساب",
  "auth.signout": "تسجيل الخروج",
  "auth.email": "البريد الإلكتروني",
  "auth.password": "كلمة المرور",
  "auth.name": "الاسم المعروض",
  "auth.noAccount": "ليس لديك حساب؟",
  "auth.haveAccount": "لديك حساب بالفعل؟",
  "auth.passwordHint": "عشرة أحرف على الأقل.",
  "common.save": "حفظ",
  "common.cancel": "إلغاء",
  "common.create": "إنشاء",
  "common.delete": "حذف",
  "common.download": "تنزيل",
  "common.loading": "جارٍ التحميل…",
  "common.search": "بحث",
  "common.new": "جديد",
  "common.send": "إرسال",
  "common.stop": "إيقاف",
  "common.retry": "إعادة المحاولة",
  "common.status": "الحالة",
  "common.empty": "لا يوجد شيء هنا بعد.",
  "common.project": "المشروع",
  "common.noProject": "بدون مشروع",
  "common.workspace": "مساحة العمل",
  "common.language": "اللغة",
  "common.theme": "المظهر",
  "common.dark": "داكن",
  "common.light": "فاتح",
  "chat.placeholder": "اكتب رسالتك إلى مايند…",
  "chat.new": "محادثة جديدة",
  "chat.auto": "تلقائي",
  "chat.manual": "يدوي",
  "chat.attach": "إرفاق ملفات",
  "chat.regenerate": "إعادة التوليد",
  "chat.edit": "تعديل",
  "chat.export": "تصدير",
  "chat.noModel": "لا يوجد نموذج محادثة مفعّل بعد. يمكن للمسؤول إضافة مزوّد من الإعدادات.",
  "chat.empty": "اسأل أي شيء. أرفق مستندات أو صورًا لمناقشتها.",
  "dashboard.welcome": "مرحبًا بعودتك",
  "dashboard.projects": "أحدث المشاريع",
  "dashboard.jobs": "أحدث المهام",
  "dashboard.capabilities": "توفر الميزات",
  "projects.new": "مشروع جديد",
  "projects.name": "اسم المشروع",
  "projects.description": "الوصف",
  "projects.visibility": "الظهور",
  "projects.private": "خاص",
  "projects.org": "كل المؤسسة",
  "cad.title": "قطع ثلاثية الأبعاد بارامترية",
  "cad.generate": "توليد القطعة",
  "cad.describe": "صف القطعة",
  "docs.title": "استوديو المستندات",
  "docs.generate": "توليد الملف",
  "team.members": "الأعضاء",
  "team.invite": "دعوة عضو",
  "settings.providers": "مزوّدو الذكاء الاصطناعي",
  "settings.models": "النماذج",
  "settings.budgets": "الميزانيات والسياسات",
  "settings.integrations": "التكاملات",
};

const dictionaries: Record<Locale, Record<MessageKey, string>> = { en, ar };

type Ctx = { locale: Locale; dir: "ltr" | "rtl"; setLocale: (l: Locale) => void; t: (k: MessageKey) => string };
const I18nContext = createContext<Ctx | null>(null);

function detect(): Locale {
  try {
    const saved = localStorage.getItem("mind.locale");
    if (saved === "en" || saved === "ar") return saved;
  } catch {
    /* storage unavailable */
  }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ar") ? "ar" : "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");
  useEffect(() => setLocaleState(detect()), []);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
  }, [locale]);
  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      localStorage.setItem("mind.locale", l);
    } catch {
      /* ignore */
    }
  }, []);
  const value = useMemo<Ctx>(
    () => ({ locale, dir: locale === "ar" ? "rtl" : "ltr", setLocale, t: (k) => dictionaries[locale][k] ?? en[k] }),
    [locale, setLocale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Ctx {
  const c = useContext(I18nContext);
  if (!c) throw new Error("useI18n outside I18nProvider");
  return c;
}
