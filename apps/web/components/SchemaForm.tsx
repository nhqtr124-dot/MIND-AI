"use client";

import { Field, Input } from "./ui";

type Prop = { type?: string; title?: string; description?: string; default?: unknown; minimum?: number; maximum?: number; exclusiveMinimum?: number };
export type JsonSchema = { properties?: Record<string, Prop>; required?: string[] };

/** Renders number/integer/boolean/string inputs from a JSON schema (Pydantic model_json_schema). */
export function SchemaForm({ schema, value, onChange }: { schema: JsonSchema; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const props = Object.entries(schema.properties ?? {});
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {props.map(([key, p]) => {
        const label = p.title ?? key.replaceAll("_", " ");
        const current = value[key] ?? p.default;
        const hint = [p.description, p.minimum != null || p.maximum != null ? `range ${p.minimum ?? p.exclusiveMinimum ?? "…"}–${p.maximum ?? "…"}` : null].filter(Boolean).join(" · ");
        if (p.type === "boolean") {
          return (
            <label key={key} className="flex items-center gap-2 text-sm sm:col-span-2">
              <input type="checkbox" checked={Boolean(current)} onChange={(e) => onChange({ ...value, [key]: e.target.checked })} className="size-4 accent-[var(--mind-primary)]" />
              <span>{label}</span>
              {p.description && <span className="text-xs text-muted">— {p.description}</span>}
            </label>
          );
        }
        const numeric = p.type === "number" || p.type === "integer";
        return (
          <Field key={key} label={label} hint={hint || undefined}>
            <Input
              type={numeric ? "number" : "text"}
              step={p.type === "integer" ? 1 : "any"}
              min={p.minimum ?? p.exclusiveMinimum}
              max={p.maximum}
              value={current == null ? "" : String(current)}
              onChange={(e) => {
                const raw = e.target.value;
                onChange({ ...value, [key]: numeric ? (raw === "" ? undefined : Number(raw)) : raw });
              }}
            />
          </Field>
        );
      })}
    </div>
  );
}
