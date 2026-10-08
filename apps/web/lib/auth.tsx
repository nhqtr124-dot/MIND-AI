"use client";

import type { Org, User } from "@mind/shared-types";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, mind } from "./api";

type AuthState = {
  user: User | null;
  orgs: Org[];
  org: Org | null;
  loading: boolean;
  setOrgId: (id: string) => void;
  reload: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [orgId, setOrgIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const reload = useCallback(async () => {
    try {
      const [{ data: me }, { data: list }] = await Promise.all([api.GET("/api/v1/auth/me"), api.GET("/api/v1/orgs")]);
      setUser(me ?? null);
      setOrgs(list ?? []);
      let saved: string | null = null;
      try {
        saved = localStorage.getItem("mind.org");
      } catch {
        /* ignore */
      }
      setOrgIdState((cur) => {
        const pick = cur ?? saved;
        return list?.some((o) => o.id === pick) ? pick : (list?.[0]?.id ?? null);
      });
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const setOrgId = useCallback((id: string) => {
    setOrgIdState(id);
    try {
      localStorage.setItem("mind.org", id);
    } catch {
      /* ignore */
    }
  }, []);

  const logout = useCallback(async () => {
    await mind.logout().catch(() => undefined);
    setUser(null);
    router.push("/login");
  }, [router]);

  const org = orgs.find((o) => o.id === orgId) ?? null;
  return <AuthContext.Provider value={{ user, orgs, org, loading, setOrgId, reload, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const c = useContext(AuthContext);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}

/** The current org; components under the app shell can rely on it being set. */
export function useOrg(): Org {
  const { org } = useAuth();
  if (!org) throw new Error("no organization selected");
  return org;
}
