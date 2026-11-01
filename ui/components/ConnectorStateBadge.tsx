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
 *
 * REQ-067 item 9: the scheduler skips a connector whose repair waits for
 * an operator decision, so "last sync 3 m ago" on such a connector reads
 * as broken. When the server says the connector needs a decision, the
 * age line instead says the syncs are paused until it gets one.
 */
/** Server activity says syncs wait on an operator decision (connector_state.py). */
function syncsPaused(state: ConnectorState, activity: string | null | undefined): boolean {
  return (
    (state === "error" && activity === "needs your decision") ||
    (state === "degrading" && (activity === "fix ready · verified · 0 model calls" || activity === "fix waiting for review"))
  );
}

/**
 * The badge's activity and sync-age lines on their own, so a layout can
 * place them apart from the state word (the Connectors list shows them
 * under its Last sync column).
 */
export function ConnectorActivity({
  state,
  activity,
  lastSyncAt,
  compact = false,
}: Props & {
  /** Under a "Last sync <age>" label: no "in sync · Complete …" line and no
   * age line (the label and the block above already say both). */
  compact?: boolean;
}) {
  const now = useNow(1000);
  // "in sync · Complete 2 s ago" already says when it last synced; a second
  // "last sync 3 s ago" only repeats it. Other activities keep the age.
  const inSync = (activity ?? "").startsWith("in sync");
  const age = compact || inSync ? null : ageAgo(lastSyncAt ?? null, now);
  // The scheduler skips these connectors, so the stale age would mislead.
  const paused = syncsPaused(state, activity);
  const showActivity = Boolean(activity) && !(compact && (activity ?? "").startsWith("in sync"));
  return (
    <>
      {showActivity ? (
        <span data-testid="connector-state-activity" className="font-normal text-ink-2">
          {activity}
        </span>
      ) : null}
      {paused ? (
        <span data-testid="connector-state-sync-age" className="num font-normal text-ink-2">
          syncs paused until you decide
        </span>
      ) : age ? (
        <span data-testid="connector-state-sync-age" className="num font-normal text-ink-2">
          last sync {age}
        </span>
      ) : null}
    </>
  );
}

export function ConnectorStateBadge({
  state,
  activity,
  lastSyncAt,
  wordOnly = false,
}: Props & {
  /** Only colour + shape + word; the caller renders <ConnectorActivity> elsewhere. */
  wordOnly?: boolean;
}) {
  const word = WORD[state] ?? state;
  const cls = CLASS[state] ?? "cs-unknown";
  const shape = SHAPE[state] ?? "hollow";
  return (
    <span data-testid="connector-state" data-state={state} data-shape={shape} className={`cs ${cls}`}>
      <span aria-hidden="true" className="sr-only">
        {shape}
      </span>
      {word}
      {wordOnly ? null : <ConnectorActivity state={state} activity={activity} lastSyncAt={lastSyncAt} />}
    </span>
  );
}

/** Ticking "2 s ago" for a sync timestamp; empty when there is none. */
export function SyncAge({ at }: { at: string | null | undefined }) {
  const now = useNow(1000);
  const age = ageAgo(at ?? null, now);
  return age ? <span data-testid="connector-sync-age-label">{age}</span> : null;
}
