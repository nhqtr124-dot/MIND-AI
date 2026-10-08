import type { Artifact, Conversation, Message, Org, Project, User } from "@mind/shared-types";
import { palette } from "@mind/ui";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  I18nManager,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useColorScheme,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { api, mind, msg, secureTokenStore } from "./client";

type Screen = { name: "projects" } | { name: "project"; project: Project } | { name: "chats" } | { name: "chat"; conversation: Conversation };

export default function App() {
  const scheme = useColorScheme() === "light" ? "light" : "dark";
  const c = palette[scheme];
  const [user, setUser] = useState<User | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [org, setOrg] = useState<Org | null>(null);
  const [booting, setBooting] = useState(true);
  const [screen, setScreen] = useState<Screen>({ name: "projects" });

  const loadSession = useCallback(async () => {
    try {
      const [{ data: me }, { data: list }] = await Promise.all([api.GET("/api/v1/auth/me"), api.GET("/api/v1/orgs")]);
      setUser(me ?? null);
      setOrgs(list ?? []);
      setOrg((cur) => cur ?? list?.[0] ?? null);
    } catch {
      setUser(null);
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    secureTokenStore.get().then((t) => (t ? loadSession() : setBooting(false)));
  }, [loadSession]);

  const s = styles(c);
  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.root}>
        <StatusBar style={scheme === "dark" ? "light" : "dark"} />
        {booting ? (
          <ActivityIndicator style={{ flex: 1 }} color={c.primary} />
        ) : !user || !org ? (
          <Auth c={c} onDone={loadSession} />
        ) : (
          <View style={{ flex: 1 }}>
            <View style={s.header}>
              <Text style={s.brand}>MIND AI</Text>
              <Pressable onPress={async () => { await mind.logout(); setUser(null); }}>
                <Text style={s.link}>Sign out</Text>
              </Pressable>
            </View>
            <ScrollView horizontal style={{ flexGrow: 0 }} contentContainerStyle={{ gap: 8, paddingHorizontal: 16, paddingBottom: 8 }}>
              {orgs.map((o) => (
                <Pressable key={o.id} onPress={() => { setOrg(o); setScreen({ name: "projects" }); }} style={[s.chip, o.id === org.id && s.chipActive]}>
                  <Text style={[s.chipText, o.id === org.id && { color: c.primaryText }]}>{o.name}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <View style={s.tabs}>
              {(["projects", "chats"] as const).map((t) => (
                <Pressable key={t} onPress={() => setScreen(t === "projects" ? { name: "projects" } : { name: "chats" })} style={[s.tab, screen.name.startsWith(t.slice(0, 4)) && s.tabActive]}>
                  <Text style={s.tabText}>{t === "projects" ? "Projects" : "Chat"}</Text>
                </Pressable>
              ))}
            </View>
            {screen.name === "projects" && <Projects c={c} org={org} open={(p) => setScreen({ name: "project", project: p })} />}
            {screen.name === "project" && <ProjectView c={c} project={screen.project} back={() => setScreen({ name: "projects" })} />}
            {screen.name === "chats" && <Chats c={c} org={org} open={(cv) => setScreen({ name: "chat", conversation: cv })} />}
            {screen.name === "chat" && <ChatView c={c} conversation={screen.conversation} back={() => setScreen({ name: "chats" })} />}
          </View>
        )}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

type C = (typeof palette)["dark"] | (typeof palette)["light"];

function Auth({ c, onDone }: { c: C; onDone: () => void }) {
  const s = styles(c);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={s.center}>
      <Text style={[s.brand, { fontSize: 32, marginBottom: 4 }]}>MIND AI</Text>
      <Text style={[s.muted, { marginBottom: 24 }]}>{mode === "login" ? "Sign in to your workspace" : "Create an account"}</Text>
      {mode === "register" && <TextInput style={s.input} placeholder="Display name" placeholderTextColor={c.muted} value={name} onChangeText={setName} />}
      <TextInput style={s.input} placeholder="Email" placeholderTextColor={c.muted} autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
      <TextInput style={s.input} placeholder="Password (10+ characters)" placeholderTextColor={c.muted} secureTextEntry value={password} onChangeText={setPassword} />
      {error && <Text style={s.error}>{error}</Text>}
      <Pressable
        style={[s.button, busy && { opacity: 0.6 }]}
        disabled={busy}
        onPress={async () => {
          setBusy(true);
          setError(null);
          try {
            if (mode === "login") await mind.login(email.trim(), password);
            else await mind.register({ email: email.trim(), password, display_name: name.trim() || email.split("@")[0], locale: I18nManager.isRTL ? "ar" : "en" });
            onDone();
          } catch (e) {
            setError(msg(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Text style={s.buttonText}>{mode === "login" ? "Sign in" : "Create account"}</Text>
      </Pressable>
      <Pressable onPress={() => setMode(mode === "login" ? "register" : "login")} style={{ marginTop: 16 }}>
        <Text style={s.link}>{mode === "login" ? "No account? Create one" : "Have an account? Sign in"}</Text>
      </Pressable>
    </KeyboardAvoidingView>
  );
}

function Projects({ c, org, open }: { c: C; org: Org; open: (p: Project) => void }) {
  const s = styles(c);
  const [items, setItems] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setItems(null);
    api.GET("/api/v1/projects", { params: { query: { org_id: org.id } } }).then(({ data }) => setItems(data ?? [])).catch((e) => setError(msg(e)));
  }, [org.id]);
  if (error) return <Text style={s.error}>{error}</Text>;
  if (!items) return <ActivityIndicator color={c.primary} />;
  return (
    <FlatList
      data={items}
      keyExtractor={(p) => p.id}
      contentContainerStyle={{ padding: 16, gap: 10 }}
      ListEmptyComponent={<Text style={s.muted}>No projects in this workspace yet. Create one on the web.</Text>}
      renderItem={({ item }) => (
        <Pressable style={s.card} onPress={() => open(item)}>
          <Text style={s.title}>{item.name}</Text>
          {!!item.description && <Text style={s.muted} numberOfLines={2}>{item.description}</Text>}
          <Text style={s.small}>{item.visibility} · {item.role}</Text>
        </Pressable>
      )}
    />
  );
}

function ProjectView({ c, project, back }: { c: C; project: Project; back: () => void }) {
  const s = styles(c);
  const [items, setItems] = useState<Artifact[] | null>(null);
  useEffect(() => {
    api.GET("/api/v1/artifacts", { params: { query: { project_id: project.id } } }).then(({ data }) => setItems(data ?? []));
  }, [project.id]);
  return (
    <View style={{ flex: 1 }}>
      <Pressable onPress={back} style={{ paddingHorizontal: 16 }}><Text style={s.link}>‹ Projects</Text></Pressable>
      <Text style={[s.title, { paddingHorizontal: 16, fontSize: 22, marginTop: 8 }]}>{project.name}</Text>
      {!items ? (
        <ActivityIndicator color={c.primary} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(a) => a.id}
          contentContainerStyle={{ padding: 16, gap: 10 }}
          ListEmptyComponent={<Text style={s.muted}>No generated artifacts yet.</Text>}
          renderItem={({ item }) => {
            const files = item.versions?.[item.versions.length - 1]?.files ?? [];
            return (
              <View style={s.card}>
                <Text style={s.title}>{item.title}</Text>
                <Text style={s.small}>{item.kind} · {item.status}{item.validation_status ? ` · ${item.validation_status}` : ""}</Text>
                {files.map((f) => (
                  <Pressable
                    key={f.name}
                    onPress={async () => {
                      // Signed, expiring link: the system browser downloads without needing our token.
                      const url = await mind.signedUrl(item.id, f.name);
                      await Linking.openURL(url);
                    }}
                  >
                    <Text style={s.link}>⬇ {f.name}</Text>
                  </Pressable>
                ))}
              </View>
            );
          }}
        />
      )}
    </View>
  );
}

function Chats({ c, org, open }: { c: C; org: Org; open: (cv: Conversation) => void }) {
  const s = styles(c);
  const [items, setItems] = useState<Conversation[] | null>(null);
  const load = useCallback(() => api.GET("/api/v1/conversations", { params: { query: { org_id: org.id } } }).then(({ data }) => setItems(data ?? [])), [org.id]);
  useEffect(() => void load(), [load]);
  return (
    <View style={{ flex: 1 }}>
      <Pressable style={[s.button, { marginHorizontal: 16 }]} onPress={async () => { const { data } = await api.POST("/api/v1/conversations", { body: { org_id: org.id } }); open(data!); }}>
        <Text style={s.buttonText}>New chat</Text>
      </Pressable>
      <FlatList
        data={items ?? []}
        keyExtractor={(x) => x.id}
        contentContainerStyle={{ padding: 16, gap: 8 }}
        renderItem={({ item }) => (
          <Pressable style={s.card} onPress={() => open(item)}>
            <Text style={s.title} numberOfLines={1}>{item.title}</Text>
          </Pressable>
        )}
      />
    </View>
  );
}

function pathOf(messages: Message[], leaf: string | null | undefined): Message[] {
  const byId = new Map(messages.map((m) => [m.id, m]));
  const out: Message[] = [];
  let cur = leaf ? byId.get(leaf) : undefined;
  while (cur) {
    out.unshift(cur);
    cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
  }
  return out;
}

function ChatView({ c, conversation, back }: { c: C; conversation: Conversation; back: () => void }) {
  const s = styles(c);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/conversations/{conv_id}", { params: { path: { conv_id: conversation.id } } });
    if (data) setMessages(pathOf(data.messages, data.current_leaf_id));
  }, [conversation.id]);
  useEffect(() => void load(), [load]);

  async function send() {
    const content = text.trim();
    if (!content) return;
    setText("");
    setBusy(true);
    setError(null);
    try {
      // React Native's fetch has no streaming body support, so mobile uses the non-streaming mode.
      const { data } = await api.POST("/api/v1/conversations/{conv_id}/messages", {
        params: { path: { conv_id: conversation.id } },
        body: { content, stream: false },
      });
      const result = data as unknown as { events: { type: string; error?: { message: string } }[] };
      const err = result.events.find((e) => e.type === "error");
      if (err) setError(err.error?.message ?? "The model call failed");
      await load();
    } catch (e) {
      setError(msg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <Pressable onPress={back} style={{ paddingHorizontal: 16 }}><Text style={s.link}>‹ Chats</Text></Pressable>
      <FlatList
        data={messages}
        keyExtractor={(m) => m.id}
        contentContainerStyle={{ padding: 16, gap: 10 }}
        renderItem={({ item }) => (
          <View style={[s.bubble, item.role === "user" ? s.userBubble : null]}>
            <Text style={item.status === "failed" ? s.error : s.text}>{item.status === "failed" ? `No answer: ${(item.error as { message?: string } | null)?.message ?? "error"}` : item.content}</Text>
            {item.role === "assistant" && item.model_name && <Text style={s.small}>{item.model_name}</Text>}
          </View>
        )}
      />
      {error && <Text style={[s.error, { paddingHorizontal: 16 }]}>{error}</Text>}
      <View style={s.composer}>
        <TextInput style={[s.input, { flex: 1, marginBottom: 0 }]} placeholder="Message MIND AI…" placeholderTextColor={c.muted} value={text} onChangeText={setText} multiline />
        <Pressable style={[s.button, { paddingHorizontal: 16 }]} onPress={send} disabled={busy}>
          {busy ? <ActivityIndicator color={c.primaryText} /> : <Text style={s.buttonText}>Send</Text>}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = (c: C) =>
  StyleSheet.create({
    root: { flex: 1, backgroundColor: c.bg },
    center: { flex: 1, justifyContent: "center", padding: 24 },
    header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 16 },
    brand: { color: c.text, fontSize: 22, fontWeight: "700" },
    text: { color: c.text, fontSize: 15, lineHeight: 21 },
    title: { color: c.text, fontSize: 16, fontWeight: "600" },
    muted: { color: c.muted, fontSize: 14 },
    small: { color: c.muted, fontSize: 12, marginTop: 4 },
    link: { color: c.accent, fontSize: 15, paddingVertical: 4 },
    error: { color: c.danger, marginVertical: 8 },
    input: { borderWidth: 1, borderColor: c.border, backgroundColor: c.surface, color: c.text, borderRadius: 10, padding: 12, marginBottom: 12, fontSize: 15 },
    button: { backgroundColor: c.primary, borderRadius: 10, padding: 14, alignItems: "center", justifyContent: "center" },
    buttonText: { color: c.primaryText, fontWeight: "600", fontSize: 15 },
    card: { backgroundColor: c.surface, borderColor: c.border, borderWidth: 1, borderRadius: 14, padding: 14 },
    chip: { borderWidth: 1, borderColor: c.border, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6 },
    chipActive: { backgroundColor: c.primary, borderColor: c.primary },
    chipText: { color: c.text, fontSize: 13 },
    tabs: { flexDirection: "row", marginHorizontal: 16, marginBottom: 8, backgroundColor: c.surface2, borderRadius: 10, padding: 4 },
    tab: { flex: 1, padding: 8, borderRadius: 8, alignItems: "center" },
    tabActive: { backgroundColor: c.surface },
    tabText: { color: c.text, fontWeight: "500" },
    bubble: { backgroundColor: c.surface, borderRadius: 14, padding: 12, alignSelf: "flex-start", maxWidth: "90%" },
    userBubble: { backgroundColor: c.surface2, alignSelf: "flex-end" },
    composer: { flexDirection: "row", gap: 8, padding: 12, borderTopWidth: 1, borderTopColor: c.border, alignItems: "flex-end" },
  });
