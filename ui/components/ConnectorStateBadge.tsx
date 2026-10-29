"use client";

import { ageAgo, useNow } from "@/lib/use-now";

export type ConnectorState = "working" | "degrading" | "error" | string;

const WORD: Record<string, string> = {
  working: "Working",
  degrading: "Degrading",
  error: "Error",
};

const CLASS: Record<string, string> = {
  working: "cs-ok",
  degrading: "cs-deg",
  error: "cs-err",
};

const SHAPE: Record<string, string> = {
  working: "circle",
  degrading: "triangle",
  error: "square",
};

type Props = {
  state: ConnectorState;
  /** One-line activity from the server ("drift detected 4 s ago · …"). */
  activity?: string | null;
  /** ISO timestamp of the latest sync; rendered ticking ("last sync 4 s ago"). */
  lastSyncAt?: string | null;
};

/**
 * Connector state badge (REQ-032 §1): colour + shape + word, never colour
 * alone, plus the server's activity line. `data-state` carries the raw
 * state for tests; `data-shape` pins the distinct shape per state.
 */
export function ConnectorStateBadge({ state, activity, lastSyncAt }: Props) {
  const now = useNow(1000);
  const word = WORD[state] ?? state;
  const cls = CLASS[state] ?? "cs-unknown";
  const shape = SHAPE[state] ?? "hollow";
  const age = ageAgo(lastSyncAt ?? null, now);
  return (
    <span data-testid="connector-state" data-state={state} data-shape={shape} className={`cs ${cls}`}>
      <span aria-hidden="true" className="sr-only">
        {shape}
      </span>
      {word}
      {activity ? (
        <span data-testid="connector-state-activity" className="font-normal text-ink-2">
          {activity}
        </span>
      ) : null}
      {age ? (
        <span data-testid="connector-state-sync-age" className="num font-normal text-ink-2">
          last sync {age}
        </span>
      ) : null}
    </span>
  );
}
