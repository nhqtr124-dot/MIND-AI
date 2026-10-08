import { MindClient, type TokenStore } from "@mind/api-client";
import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";

const KEY = "mind.tokens";

/** Tokens live in the platform keystore (Keychain / Android Keystore), never in plain storage. */
export const secureTokenStore: TokenStore = {
  async get() {
    const raw = await SecureStore.getItemAsync(KEY);
    return raw ? (JSON.parse(raw) as { access: string; refresh: string }) : null;
  },
  async set(t) {
    if (t) await SecureStore.setItemAsync(KEY, JSON.stringify(t));
    else await SecureStore.deleteItemAsync(KEY);
  },
};

// 10.0.2.2 reaches the host machine from the Android emulator; override via app.json "extra.apiUrl".
export const API_URL: string = (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl ?? "http://localhost:8000";

export const mind = new MindClient({ baseUrl: API_URL, auth: "bearer", tokenStore: secureTokenStore });
export const api = mind.api;

export function msg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
