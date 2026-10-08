"use client";

import type { Job } from "@mind/shared-types";
import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, mind } from "./api";

/** Load data with an async function; re-runs when deps change. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | undefined; error: string | null; loading: boolean; reload: () => Promise<void>; setData: (d: T) => void } {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

/** Track a background job until it finishes. */
export function useJob(onDone?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const track = useCallback(
    async (jobId: string) => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setRunning(true);
      try {
        const final = await mind.waitForJob(jobId, setJob, { signal: ctl.signal, intervalMs: 800 });
        onDone?.(final);
        return final;
      } finally {
        setRunning(false);
      }
    },
    [onDone],
  );
  useEffect(() => () => abort.current?.abort(), []);
  return { job, running, track, reset: () => setJob(null) };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}
