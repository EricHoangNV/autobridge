/**
 * Ladder levels from run SSE events (REQ-002 slice 3, DIRECTION.md "the climb").
 *
 * The repair page subscribes to the source sync run's `/runs/{id}/events` stream.
 * Each SSE event is named after its evidence kind (`event: <kind>`); this module
 * maps those events to ladder rungs L0–L4 so the five-segment track can advance
 * from live events instead of the prototype's timers:
 *
 * - L0 knowledge: drift-signal kinds (fingerprint/keys/types/pagination changes,
 *   invariant failures, degraded/blocked outcomes, …)
 * - L1 rules: any event whose payload names the matched `rule_id`
 * - L2 small model: `hypothesis` / `probe` / `observation` kinds
 * - L3 frontier model: payloads carrying spent `frontier_tokens` / `frontier_calls`
 * - L4 you: a `decision` payload with `decision: "escalate"`
 *
 * An explicit numeric `ladder_level` / `ladder_level_reached` / `level` payload
 * field (0–4) always wins, so future engine-emitted ladder events are honored.
 * Unrelated kinds (`done`, `message`, unknown) return null and never move the rung.
 */

export const LADDER_EVENT_KINDS: readonly string[] = [
  "hypothesis",
  "probe",
  "observation",
  "decision",
  "fingerprint_changed",
  "keys_changed",
  "types_changed",
  "pagination_changed",
  "repeated_cursor",
  "invariant_failure",
  "no_loss",
  "unknown_enum",
  "zero_records",
  "outcome_degraded",
  "outcome_blocked",
  "canary_failed",
  "done",
];

const DRIFT_SIGNAL_KINDS: ReadonlySet<string> = new Set([
  "fingerprint_changed",
  "keys_changed",
  "types_changed",
  "pagination_changed",
  "repeated_cursor",
  "invariant_failure",
  "no_loss",
  "unknown_enum",
  "zero_records",
  "outcome_degraded",
  "outcome_blocked",
  "canary_failed",
]);

export type SseRunEvent = { type: string; data: unknown };

function payloadOf(data: unknown): Record<string, unknown> | null {
  if (data === null || typeof data !== "object") return null;
  const outer = data as Record<string, unknown>;
  const inner = outer["payload"];
  if (inner !== null && typeof inner === "object") return inner as Record<string, unknown>;
  return outer;
}

function toFiniteNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/**
 * Map one SSE event to the ladder rung it proves reached, or null when the
 * event says nothing about ladder progress.
 */
export function ladderLevelFromEvent(type: string, data: unknown): number | null {
  if (type === "done" || type === "message") return null;
  const payload = payloadOf(data);
  if (payload === null) return null;

  // Explicit engine level always wins.
  for (const key of ["ladder_level", "ladder_level_reached", "level"] as const) {
    const n = toFiniteNumber(payload[key]);
    if (n !== null && n >= 0 && n <= 4) return Math.floor(n);
  }

  // A matched rule is the L1 fix.
  const ruleId = payload["rule_id"];
  if (typeof ruleId === "string" && ruleId.length > 0) return 1;

  // Spent frontier budget means the frontier model ran: L3.
  for (const key of ["frontier_tokens", "frontier_tokens_total", "frontier_calls"] as const) {
    const n = toFiniteNumber(payload[key]);
    if (n !== null && n > 0) return 3;
  }

  if (type === "decision") {
    // Escalation is the L4 handoff. A "propose" decision resolves the repair
    // but its rung comes from the repair record, not the event.
    return payload["decision"] === "escalate" ? 4 : null;
  }
  if (type === "hypothesis" || type === "probe" || type === "observation") return 2;
  if (DRIFT_SIGNAL_KINDS.has(type)) return 0;
  return null;
}

/** Highest ladder rung proven by a batch of SSE events, or null when none speaks. */
export function maxLadderLevel(events: SseRunEvent[]): number | null {
  let best: number | null = null;
  for (const e of events) {
    const lv = ladderLevelFromEvent(e.type, e.data);
    if (lv !== null && (best === null || lv > best)) best = lv;
  }
  return best;
}
