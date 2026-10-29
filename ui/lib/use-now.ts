"use client";

import { useEffect, useState } from "react";

/** Re-render every `everyMs` so relative ages ("last sync 4 s ago") tick. */
export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

/** Short relative age matching the server's `human_ago` (connector_state). */
export function ageAgo(iso: string | null | undefined, nowMs?: number): string | null {
  if (!iso) return null;
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return null;
  const now = nowMs ?? Date.now();
  const delta = Math.floor((now - ts) / 1000);
  if (delta < 0) return "just now";
  if (delta < 60) return `${delta} s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)} m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} h ago`;
  return `${Math.floor(delta / 86400)} d ago`;
}
