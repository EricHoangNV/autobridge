"use client";

/**
 * useRunEvents — SSE subscription for run evidence events (plan §2.11).
 * Small wrapper around EventSource that cleans up on unmount.
 *
 * The server sends one named event per evidence record (`event: <kind>`,
 * REQ-002 slice 3), so this subscribes to each ladder-relevant kind plus the
 * default `message` channel — `onmessage` alone never fires for named events.
 * No-ops on the server and where `EventSource` is unavailable (SSR, jsdom).
 */
import { useEffect, useState } from "react";
import { apiBaseUrl } from "./api/hooks";
import { LADDER_EVENT_KINDS } from "./ladder-events";

export type RunEvent = { type: string; data: unknown };

export function useRunEvents(runId: string | null, enabled = true): RunEvent[] {
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    if (!runId || !enabled) return;
    if (typeof EventSource === "undefined") return;
    const url = `${apiBaseUrl().replace(/\/$/, "")}/runs/${encodeURIComponent(runId)}/events`;
    let es: EventSource;
    try {
      es = new EventSource(url);
    } catch {
      return;
    }
    const push =
      (type: string) =>
      (e: Event): void => {
        const raw = (e as MessageEvent).data as unknown;
        try {
          const data = typeof raw === "string" ? (JSON.parse(raw) as unknown) : raw;
          setEvents((prev) => [...prev, { type, data }]);
        } catch {
          setEvents((prev) => [...prev, { type, data: raw }]);
        }
      };
    es.onmessage = push("message");
    for (const kind of LADDER_EVENT_KINDS) {
      es.addEventListener(kind, push(kind));
    }
    es.onerror = () => {
      // Keep EventSource open; browser will reconnect per spec. Close after 3 errors to avoid loops in tests.
    };
    return () => {
      es.close();
    };
  }, [runId, enabled]);

  return events;
}
