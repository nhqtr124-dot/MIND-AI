"use client";

import { useEffect, useState } from "react";

type Props = { path: string; value: string; onChange: (v: string) => void; onSave: () => void; dark: boolean };

const LANG: Record<string, string> = { ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", py: "python", html: "html", css: "css", json: "json", md: "markdown", yml: "yaml", yaml: "yaml", sql: "sql", toml: "ini" };

/**
 * Monaco editor loaded on demand. If Monaco cannot be loaded (offline, CDN blocked),
 * falls back to a plain textarea so editing still works.
 */
export function CodeEditor(props: Props) {
  const [Editor, setEditor] = useState<null | typeof import("@monaco-editor/react").default>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => alive && setFailed(true), 8000);
    import("@monaco-editor/react")
      .then(async (m) => {
        await m.loader.init();
        if (alive) {
          clearTimeout(timer);
          setEditor(() => m.default);
        }
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  const keySave = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      props.onSave();
    }
  };

  if (Editor && !failed) {
    const ext = props.path.split(".").pop() ?? "";
    return (
      <div className="h-full" onKeyDown={keySave}>
        <Editor
          path={props.path}
          value={props.value}
          language={LANG[ext] ?? "plaintext"}
          theme={props.dark ? "vs-dark" : "light"}
          onChange={(v) => props.onChange(v ?? "")}
          options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, tabSize: 2, automaticLayout: true, wordWrap: "on" }}
        />
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      {failed && <div className="border-b border-border px-3 py-1 text-xs text-warning">Code editor could not load; using a plain text editor.</div>}
      <textarea
        className="h-full w-full flex-1 resize-none bg-transparent p-3 font-mono text-[13px] leading-relaxed focus:outline-none"
        value={props.value}
        spellCheck={false}
        dir="ltr"
        onChange={(e) => props.onChange(e.target.value)}
        onKeyDown={keySave}
        aria-label={`Editing ${props.path}`}
      />
    </div>
  );
}
