"use client";

/**
 * useRunEvents — SSE subscription for run evidence events (plan §2.11).
 * Small wrapper around EventSource that cleans up on unmount.
 */
import { useEffect, useRef, useState } from "react";
import { apiBaseUrl } from "./api/hooks";

export type RunEvent = { type: string; data: unknown };

export function useRunEvents(runId: string | null, enabled = true): RunEvent[] {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!runId || !enabled) return;
    const url = `${apiBaseUrl().replace(/\/$/, "")}/runs/${encodeURIComponent(runId)}/events`;
    const es = new EventSource(url);
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as unknown;
        setEvents((prev) => [...prev, { type: e.type || "message", data }]);
      } catch {
        setEvents((prev) => [...prev, { type: "message", data: e.data }]);
      }
    };
    es.onerror = () => {
      // Keep EventSource open; browser will reconnect per spec. Close after 3 errors to avoid loops in tests.
    };
    return () => {
      es.close();
      esRef.current = null;
    };
  }, [runId, enabled]);

  return events;
}
