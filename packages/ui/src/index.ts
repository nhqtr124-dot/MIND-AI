/** MIND AI design tokens shared by the web (CSS variables) and mobile (React Native) clients. */

export const palette = {
  dark: {
    bg: "#0A0C16",
    surface: "#11142A",
    surface2: "#181C38",
    border: "#262B4D",
    text: "#E9EBF8",
    muted: "#9AA0C4",
    primary: "#7C6CFF",
    primaryText: "#FFFFFF",
    accent: "#2DD4EF",
    success: "#22C55E",
    warning: "#F5A524",
    danger: "#F04452",
  },
  light: {
    bg: "#F6F7FC",
    surface: "#FFFFFF",
    surface2: "#EEF0FA",
    border: "#DCE0F0",
    text: "#141733",
    muted: "#5A6087",
    primary: "#5B4BEB",
    primaryText: "#FFFFFF",
    accent: "#0A8FB0",
    success: "#15803D",
    warning: "#B45309",
    danger: "#C81E2E",
  },
} as const;

export type ThemeName = keyof typeof palette;
export type Palette = (typeof palette)[ThemeName];

export const radius = { sm: 6, md: 10, lg: 16, xl: 22 } as const;
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

export const statusTone: Record<string, keyof Palette> = {
  completed: "success",
  print_ready_checks_passed: "success",
  passed: "success",
  citations_verified: "success",
  running: "accent",
  planned: "muted",
  awaiting_approval: "warning",
  partially_completed: "warning",
  printable_with_warnings: "warning",
  evidence_only: "warning",
  failed: "danger",
  validation_failed: "danger",
  citations_failed: "danger",
  cancelled: "muted",
};

/** CSS custom properties for a theme, e.g. for :root and [data-theme="light"]. */
export function cssVariables(theme: ThemeName): string {
  return Object.entries(palette[theme])
    .map(([k, v]) => `--mind-${k.replace(/[A-Z0-9]/g, (c) => `-${c.toLowerCase()}`)}: ${v};`)
    .join("\n");
}
