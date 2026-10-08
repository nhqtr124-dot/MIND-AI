"use client";

import type { ChatEvent, Conversation, ConversationDetail, Message, ModelConfig } from "@mind/shared-types";
import { ChevronLeft, ChevronRight, Copy, Download, Info, MessageSquare, Paperclip, Pencil, Plus, RefreshCw, Search, Send, Square, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { Alert, Badge, Button, Empty, Input, Select, Spinner, Textarea, cx } from "@/components/ui";
import { api, errorMessage, mind } from "@/lib/api";
import { useOrg } from "@/lib/auth";
import { timeAgo, useLoad } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Pending = { files: { id: string; name: string }[] };
type Routing = Extract<ChatEvent, { type: "routing" }>;
type ErrorEvent = Extract<ChatEvent, { type: "error" }>;

function pathTo(messages: Message[], leaf: string | null): Message[] {
  const byId = new Map(messages.map((m) => [m.id, m]));
  const out: Message[] = [];
  let cur = leaf ? byId.get(leaf) : undefined;
  while (cur) {
    out.unshift(cur);
    cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
  }
  return out;
}

function deepestLeaf(messages: Message[], start: Message): Message {
  let cur = start;
  for (;;) {
    const kids = messages.filter((m) => m.parent_id === cur.id).sort((a, b) => a.created_at.localeCompare(b.created_at));
    if (!kids.length) return cur;
    cur = kids[kids.length - 1];
  }
}

function ChatInner() {
  const org = useOrg();
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const convId = params.get("c");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<{ conversation_id: string; conversation_title: string; snippet: string }[] | null>(null);
  const list = useLoad(async () => (await api.GET("/api/v1/conversations", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);
  const models = useLoad(async () => (await api.GET("/api/v1/models", { params: { query: { org_id: org.id, enabled_only: true, capability: "chat" } } })).data ?? [], [org.id]);
  const projects = useLoad(async () => (await api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } })).data ?? [], [org.id]);

  async function newChat() {
    const { data } = await api.POST("/api/v1/conversations", { body: { org_id: org.id } });
    await list.reload();
    router.push(`/chat?c=${data!.id}`);
  }

  async function search(q: string) {
    setQuery(q);
    if (!q.trim()) return setHits(null);
    const { data } = await api.GET("/api/v1/conversations/search", { params: { query: { org_id: org.id, q } } });
    setHits(data ?? []);
  }

  return (
    <div className="-mx-4 -my-6 flex h-[calc(100vh-0px)] md:-mx-8 max-lg:h-[calc(100vh-53px)]">
      <aside className="hidden w-72 shrink-0 flex-col border-e border-border bg-surface/40 md:flex">
        <div className="space-y-2 p-3">
          <Button className="w-full" icon={<Plus className="size-4" />} onClick={newChat}>{t("chat.new")}</Button>
          <div className="relative">
            <Search className="pointer-events-none absolute start-2.5 top-3 size-4 text-muted" />
            <Input placeholder={t("common.search")} value={query} onChange={(e) => search(e.target.value)} className="ps-8" />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {hits ? (
            hits.length ? (
              hits.map((h, i) => (
                <Link key={i} href={`/chat?c=${h.conversation_id}`} className="block rounded-[10px] px-2 py-2 hover:bg-surface-2">
                  <div className="truncate text-sm font-medium">{h.conversation_title}</div>
                  <div className="line-clamp-2 text-xs text-muted">{h.snippet.replaceAll("<<", "").replaceAll(">>", "")}</div>
                </Link>
              ))
            ) : (
              <p className="px-2 text-sm text-muted">No matches</p>
            )
          ) : (
            list.data?.map((c) => (
              <Link key={c.id} href={`/chat?c=${c.id}`} className={cx("block rounded-[10px] px-2.5 py-2 hover:bg-surface-2", c.id === convId && "bg-surface-2")}>
                <div className="truncate text-sm">{c.title}</div>
                <div className="text-[11px] text-muted">
                  {c.folder ? `${c.folder} · ` : ""}
                  {timeAgo(c.updated_at)}
                </div>
              </Link>
            ))
          )}
        </div>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        {convId ? (
          <Conversation key={convId} id={convId} models={models.data ?? []} projects={projects.data ?? []} onChanged={list.reload} />
        ) : (
          <div className="grid flex-1 place-items-center p-6">
            <Empty icon={<MessageSquare className="size-10" />} title={t("nav.chat")}>
              <p className="mb-4">{models.data && models.data.length === 0 ? t("chat.noModel") : t("chat.empty")}</p>
              <Button onClick={newChat} icon={<Plus className="size-4" />}>{t("chat.new")}</Button>
            </Empty>
          </div>
        )}
      </section>
    </div>
  );
}

function Conversation({ id, models, projects, onChanged }: { id: string; models: ModelConfig[]; projects: { id: string; name: string }[]; onChanged: () => void }) {
  const { t } = useI18n();
  const org = useOrg();
  const [conv, setConv] = useState<ConversationDetail | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending>({ files: [] });
  const [streaming, setStreaming] = useState<{ text: string; routing: Routing | null; userText: string } | null>(null);
  const [lastError, setLastError] = useState<ErrorEvent | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/conversations/{conv_id}", { params: { path: { conv_id: id } } });
    setConv(data ?? null);
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);

  const path = useMemo(() => (conv ? pathTo(conv.messages, conv.current_leaf_id) : []), [conv]);
  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [path.length, streaming?.text]);

  async function patch(body: Record<string, unknown>) {
    const { data } = await api.PATCH("/api/v1/conversations/{conv_id}", { params: { path: { conv_id: id } }, body });
    if (data) setConv((c) => (c ? { ...c, ...data } : c));
    onChanged();
  }

  async function run(stream: AsyncGenerator<ChatEvent>, userText: string) {
    setStreaming({ text: "", routing: null, userText });
    setLastError(null);
    try {
      for await (const ev of stream) {
        if (ev.type === "delta") setStreaming((s) => (s ? { ...s, text: s.text + ev.text } : s));
        else if (ev.type === "routing") setStreaming((s) => (s ? { ...s, routing: ev, text: ev.fallback ? "" : s.text } : s));
        else if (ev.type === "error") setLastError(ev);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setLastError({ type: "error", error: { kind: "network", message: errorMessage(e) } });
    } finally {
      setStreaming(null);
      abort.current = null;
      await load();
      onChanged();
    }
  }

  async function send(opts: { modelOverride?: string } = {}) {
    const content = text.trim();
    if (!content || streaming) return;
    const ctl = new AbortController();
    abort.current = ctl;
    const attachments = pending.files.map((f) => f.id);
    setText("");
    setPending({ files: [] });
    await run(mind.streamMessage(id, { content, attachments, model_config_id: opts.modelOverride ?? null }, ctl.signal), content);
  }

  async function retryWith(modelId: string) {
    const lastUser = [...path].reverse().find((m) => m.role === "user");
    const failed = [...path].reverse().find((m) => m.role === "assistant");
    if (!lastUser || !failed) return;
    const ctl = new AbortController();
    abort.current = ctl;
    await run(mind.streamRegenerate(id, failed.id, modelId, ctl.signal), lastUser.content);
  }

  async function regenerate(m: Message) {
    const ctl = new AbortController();
    abort.current = ctl;
    await run(mind.streamRegenerate(id, m.id, null, ctl.signal), "");
  }

  async function saveEdit() {
    if (!editing) return;
    const ctl = new AbortController();
    abort.current = ctl;
    const e = editing;
    setEditing(null);
    await run(mind.streamMessage(id, { content: e.text, edit_of: e.id }, ctl.signal), e.text);
  }

  async function switchSibling(m: Message, dir: -1 | 1) {
    if (!conv) return;
    const sibs = conv.messages.filter((x) => x.parent_id === m.parent_id && x.role === m.role).sort((a, b) => a.created_at.localeCompare(b.created_at));
    const next = sibs[sibs.indexOf(m) + dir];
    if (next) await patch({ current_leaf_id: deepestLeaf(conv.messages, next).id });
  }

  async function attach(files: FileList | null) {
    if (!files) return;
    setUploadErr(null);
    for (const f of Array.from(files)) {
      try {
        const info = await mind.upload(f, f.name, conv?.project_id ? { project_id: conv.project_id } : { org_id: org.id });
        setPending((p) => ({ files: [...p.files, { id: info.id, name: info.path }] }));
      } catch (e) {
        setUploadErr(`${f.name}: ${errorMessage(e)}`);
      }
    }
  }

  if (!conv) return <div className="grid flex-1 place-items-center"><Spinner /></div>;

  return (
    <>
      <header className="glass flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <input
          className="min-w-0 flex-1 bg-transparent text-base font-semibold focus:outline-none"
          defaultValue={conv.title}
          aria-label="Conversation title"
          onBlur={(e) => e.target.value.trim() && e.target.value !== conv.title && patch({ title: e.target.value.trim() })}
        />
        <Select className="h-8 w-40" value={conv.project_id ?? ""} onChange={(e) => patch({ project_id: e.target.value || null })} aria-label={t("common.project")}>
          <option value="">{t("common.noProject")}</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
        <Select className="h-8 w-28" value={conv.model_mode} onChange={(e) => (e.target.value === "manual" && !conv.model_config_id && models[0] ? patch({ model_mode: "manual", model_config_id: models[0].id }) : patch({ model_mode: e.target.value }))} aria-label="Model mode">
          <option value="auto">{t("chat.auto")}</option>
          <option value="manual" disabled={!models.length}>{t("chat.manual")}</option>
        </Select>
        {conv.model_mode === "manual" ? (
          <Select className="h-8 w-52" value={conv.model_config_id ?? ""} onChange={(e) => patch({ model_config_id: e.target.value })} aria-label="Model">
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.display_name} · {m.provider_name}</option>
            ))}
          </Select>
        ) : (
          <Select className="h-8 w-32" value={conv.preference} onChange={(e) => patch({ preference: e.target.value })} aria-label="Routing preference">
            <option value="balanced">Balanced</option>
            <option value="quality">Quality</option>
            <option value="speed">Speed</option>
            <option value="cost">Cost</option>
          </Select>
        )}
        <a href={`/api/v1/conversations/${id}/export?format=md`} className="inline-flex h-8 items-center gap-1 rounded-[8px] px-2 text-sm text-muted hover:bg-surface-2 hover:text-text">
          <Download className="size-4" /> {t("chat.export")}
        </a>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {path.length === 0 && !streaming && <Empty title={t("chat.empty")}>{models.length === 0 && <span className="text-warning">{t("chat.noModel")}</span>}</Empty>}
          {path.map((m) => {
            const sibs = conv.messages.filter((x) => x.parent_id === m.parent_id && x.role === m.role);
            const idx = sibs.sort((a, b) => a.created_at.localeCompare(b.created_at)).findIndex((x) => x.id === m.id);
            return (
              <MessageView
                key={m.id}
                m={m}
                branch={sibs.length > 1 ? { index: idx, count: sibs.length, prev: () => switchSibling(m, -1), next: () => switchSibling(m, 1) } : null}
                editing={editing?.id === m.id ? editing.text : null}
                onEdit={(txt) => setEditing(txt === null ? null : { id: m.id, text: txt })}
                onSaveEdit={saveEdit}
                onRegenerate={() => regenerate(m)}
                busy={!!streaming}
                files={conv}
              />
            );
          })}
          {streaming && (
            <>
              {streaming.userText && <div className="ms-auto max-w-[85%] rounded-[16px] rounded-ee-[4px] bg-primary/20 px-4 py-2.5 whitespace-pre-wrap">{streaming.userText}</div>}
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs text-muted">
                  <Spinner className="size-3" />
                  {streaming.routing ? (
                    <span>
                      {streaming.routing.display_name} · {streaming.routing.mode}
                      {streaming.routing.fallback && <Badge tone="warning" className="ms-2">fallback</Badge>}
                    </span>
                  ) : (
                    "routing…"
                  )}
                </div>
                {streaming.text && <Markdown>{streaming.text}</Markdown>}
              </div>
            </>
          )}
          {lastError && !streaming && (
            <Alert>
              <div className="font-medium">{lastError.error.message}</div>
              {!!lastError.fallback_options?.length && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="text-xs">Try another model (your choice — MIND never switches silently):</span>
                  {lastError.fallback_options.map((o) => (
                    <Button key={o.model_config_id} size="sm" variant="secondary" onClick={() => retryWith(o.model_config_id)}>{o.display_name}</Button>
                  ))}
                </div>
              )}
            </Alert>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className="border-t border-border p-3">
        <div className="mx-auto max-w-3xl">
          {uploadErr && <div className="mb-2"><Alert>{uploadErr}</Alert></div>}
          {pending.files.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {pending.files.map((f) => (
                <Badge key={f.id} tone="primary">
                  {f.name}
                  <button aria-label={`Remove ${f.name}`} onClick={() => setPending((p) => ({ files: p.files.filter((x) => x.id !== f.id) }))}>
                    <X className="size-3" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2 rounded-[16px] border border-border bg-surface p-2 focus-within:border-primary">
            <input ref={fileRef} type="file" multiple hidden onChange={(e) => attach(e.target.files)} />
            <button className="rounded-[10px] p-2 text-muted hover:bg-surface-2 hover:text-text" onClick={() => fileRef.current?.click()} aria-label={t("chat.attach")} title={t("chat.attach")}>
              <Paperclip className="size-5" />
            </button>
            <Textarea
              rows={1}
              className="max-h-48 min-h-10 flex-1 resize-none border-0 bg-transparent focus:border-0"
              placeholder={t("chat.placeholder")}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            {streaming ? (
              <Button variant="secondary" onClick={() => abort.current?.abort()} icon={<Square className="size-4" />} aria-label={t("common.stop")} />
            ) : (
              <Button onClick={() => send()} disabled={!text.trim()} icon={<Send className="size-4 rtl:-scale-x-100" />} aria-label={t("common.send")} />
            )}
          </div>
          <p className="mt-1.5 text-center text-[11px] text-muted">Models can be wrong. Check important facts. Attached documents are treated as data, not instructions.</p>
        </div>
      </div>
    </>
  );
}

function MessageView({
  m,
  branch,
  editing,
  onEdit,
  onSaveEdit,
  onRegenerate,
  busy,
}: {
  m: Message;
  branch: { index: number; count: number; prev: () => void; next: () => void } | null;
  editing: string | null;
  onEdit: (t: string | null) => void;
  onSaveEdit: () => void;
  onRegenerate: () => void;
  busy: boolean;
  files: Conversation;
}) {
  const { t } = useI18n();
  const [info, setInfo] = useState(false);
  const branchNav = branch && (
    <span className="inline-flex items-center gap-1 text-xs text-muted">
      <button onClick={branch.prev} disabled={branch.index === 0} aria-label="Previous version" className="disabled:opacity-30"><ChevronLeft className="size-4 rtl:rotate-180" /></button>
      {branch.index + 1}/{branch.count}
      <button onClick={branch.next} disabled={branch.index === branch.count - 1} aria-label="Next version" className="disabled:opacity-30"><ChevronRight className="size-4 rtl:rotate-180" /></button>
    </span>
  );

  if (m.role === "user") {
    return (
      <div className="group flex flex-col items-end gap-1">
        {editing !== null ? (
          <div className="w-full max-w-[85%] space-y-2">
            <Textarea rows={3} value={editing} onChange={(e) => onEdit(e.target.value)} autoFocus />
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => onEdit(null)}>{t("common.cancel")}</Button>
              <Button size="sm" onClick={onSaveEdit} disabled={!editing.trim()}>{t("common.send")}</Button>
            </div>
          </div>
        ) : (
          <div className="max-w-[85%] rounded-[16px] rounded-ee-[4px] bg-primary/20 px-4 py-2.5 whitespace-pre-wrap">
            {m.content}
            {m.attachments.length > 0 && <div className="mt-1 text-xs text-muted">📎 {m.attachments.length} attachment(s)</div>}
          </div>
        )}
        <div className="flex items-center gap-2 opacity-70 group-hover:opacity-100">
          {branchNav}
          {!busy && editing === null && (
            <button className="text-muted hover:text-text" onClick={() => onEdit(m.content)} aria-label={t("chat.edit")}><Pencil className="size-3.5" /></button>
          )}
        </div>
      </div>
    );
  }
  const routing = m.routing as { reason?: string; mode?: string; usage_estimated?: boolean; previous_failures?: { model: string; message: string }[] } | null;
  return (
    <div className="group space-y-1.5">
      {m.status === "failed" ? (
        <Alert>
          <div className="font-medium">The model call failed — no answer was generated.</div>
          <div className="text-xs">{String((m.error as { message?: string } | null)?.message ?? "unknown error")}</div>
        </Alert>
      ) : m.status === "cancelled" ? (
        <div>
          {m.content && <Markdown>{m.content}</Markdown>}
          <Badge tone="muted">stopped</Badge>
        </div>
      ) : (
        <Markdown>{m.content}</Markdown>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted opacity-70 group-hover:opacity-100">
        {branchNav}
        {m.model_name && <span>{m.model_name}</span>}
        {m.input_tokens != null && (
          <span title={routing?.usage_estimated ? "Estimated: provider did not report usage" : "Reported by the provider"}>
            {m.input_tokens}+{m.output_tokens} tok{routing?.usage_estimated ? " (est.)" : ""}
          </span>
        )}
        {m.cost_usd != null && <span>${Number(m.cost_usd).toFixed(5)}</span>}
        {!busy && (
          <button className="hover:text-text" onClick={onRegenerate} aria-label={t("chat.regenerate")} title={t("chat.regenerate")}><RefreshCw className="size-3.5" /></button>
        )}
        <button className="hover:text-text" onClick={() => navigator.clipboard.writeText(m.content)} aria-label="Copy"><Copy className="size-3.5" /></button>
        {routing && <button className="hover:text-text" onClick={() => setInfo((v) => !v)} aria-label="Routing details"><Info className="size-3.5" /></button>}
      </div>
      {info && routing && (
        <div className="rounded-[10px] border border-border bg-surface-2/50 p-2 text-xs text-muted">
          <div>Mode: {routing.mode}</div>
          <div>{routing.reason}</div>
          {routing.previous_failures?.map((f, i) => (
            <div key={i} className="text-warning">Failed first: {f.model} — {f.message}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <ChatInner />
    </Suspense>
  );
}
