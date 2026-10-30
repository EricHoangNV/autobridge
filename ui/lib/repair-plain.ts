/**
 * REQ-056 — plain-language lookup tables for the repair page.
 *
 * Facts only (REQ-054): no per-drift narrative sentences. Plain wording
 * comes from these two small tables plus the real numbers from the API.
 * Every code used anywhere has an entry; unknown codes fall back to the
 * code itself, never blank.
 *
 * All wording is written once, at build time (owner decision (a)): no
 * model call at runtime. Deck 3 stays "0 model calls".
 * Plain words a hotel operator understands, no internal codes,
 * <= 8 words per headline.
 */

/** Drift class -> plain name (<= 8 words each). */
export const DRIFT_PLAIN: Record<string, string> = {
  SEMANTIC_VALUE_DRIFT: "Values changed meaning",
  PAGINATION_CHANGE: "Paging changed",
  PAGINATION_BUG: "Paging is broken",
  SCHEMA_RENAME: "A field was renamed",
  SCHEMA_REMOVED: "A field disappeared",
  SCHEMA_TYPE_CHANGE: "A field changed shape",
  STATUS_VOCAB: "Status codes changed",
  DUPLICATE_DELIVERY: "Records arrive twice",
  RATE_LIMIT: "The vendor is throttling us",
  TRANSIENT_ERROR: "The vendor is briefly down",
  CLOUD_SLOW: "The connection is slow",
  AUTH_EXPIRED: "Access expired",
  AUTH_REVOKED: "Access was revoked",
  CAPABILITY_REMOVED: "The vendor removed something we use",
  PROTOCOL_VIOLATION: "The device broke the protocol",
  LINK_RESYNC: "The device link dropped",
  ACK_TIMEOUT: "The device stopped answering",
  SESSION_LOST: "The device session dropped mid-command",
  STALE_COMMAND: "An outdated command arrived",
  AGENT_RESTART: "The on-site agent restarted",
  UNKNOWN: "Something changed",
};

/** Check kind -> plain meaning (a clause, not a headline). */
export const CHECK_PLAIN: Record<string, string> = {
  money_invariant: "amounts don't add up to the vendor's total",
  count_invariant: "record counts don't match",
  no_loss: "records went missing",
  unique_key: "duplicate records",
  date_order: "dates are out of order",
  business_date_invariant: "postings land on the wrong business day",
  http_error: "the vendor's API returned errors",
  feed_unavailable: "the device feed is unavailable",
};

/** Ladder rung -> plain chip label (L0..L4). */
export const RUNG_CHIP: Record<number, string> = {
  0: "Known fix",
  1: "Rule",
  2: "Small model",
  3: "AI model",
  4: "You",
};

/** Rung that held -> step-2 headline (<= 8 words). */
export const RUNG_HEADLINE: Record<number, string> = {
  0: "Fixed from a known fix",
  1: "Fixed by a known rule",
  2: "Fixed by the small model",
  3: "Fixed by the AI model",
  4: "Needs your decision",
};

/** Repair decision -> step-3 / rail headline (<= 8 words). */
export const STATE_HEADLINE: Record<string, string> = {
  propose: "Fix ready to approve",
  climbing: "Still repairing",
  escalate: "Needs your decision",
  approved: "Approved and shipped",
  reject: "Rejected, nothing changed",
  rolled_back: "Rolled back safely",
  failed: "Repair failed safely",
};

/** Decision rail top line per repair state (one line). */
export const RAIL_LINE: Record<string, string> = {
  propose: "Fix ready — approve to ship it",
  climbing: "Repairing — no action yet",
  escalate: "Needs your decision",
  escalate_proposal: "Tested proposal — your call",
  approved: "Approved — shipped",
  reject: "Rejected — drift stays open",
  rolled_back: "Rolled back — drift stays open",
  failed: "Failed — nothing was shipped",
};

/** Dry-run lines (always shown when a proposal exists). */
export const DRY_RUN_PASSED = "Our test on today's records: passed";
export function dryRunRetried(firstNote: string): string {
  const short = firstNote.length > 90 ? `${firstNote.slice(0, 87)}…` : firstNote;
  return short
    ? `First idea rejected by our test (${short}); second idea passed`
    : "First idea rejected by our test; second idea passed";
}

export function driftPlain(code: string | null | undefined): string {
  if (!code) return "Drift detected";
  return DRIFT_PLAIN[code] ?? code;
}

export function checkPlain(kind: string | null | undefined): string {
  if (!kind) return "a check failed";
  return CHECK_PLAIN[kind] ?? kind;
}

export function rungChip(rung: number): string {
  return RUNG_CHIP[rung] ?? `L${rung}`;
}

export function rungHeadline(rung: number, aiUsed: boolean): string {
  if (rung <= 1) return "Fixed by a known rule — no AI needed";
  if (rung === 2) return "Small model ranked it — no AI needed";
  if (rung === 3) return aiUsed ? "AI model proposed a fix" : "AI model was consulted";
  return "Needs your decision";
}

export function stateHeadline(decision: string): string {
  return STATE_HEADLINE[decision] ?? decision;
}

export function railLine(decision: string, hasProposal: boolean): string {
  if (decision === "escalate" && hasProposal) return RAIL_LINE["escalate_proposal"];
  return RAIL_LINE[decision] ?? decision;
}

export type PatchShape = { op: string; path: string };

/**
 * "What changes" phrasing per patch shape (plain words, <= 8 words
 * headline + one clause). From the op + path only, never the values.
 */
export function patchChangePlain(op: string, path: string): string {
  const segs = path.split("/").filter(Boolean);
  const last = segs[segs.length - 1] ?? "setting";
  if (last === "compare_to") return "Compare against another total";
  if (path.includes("/pagination")) return op === "remove" ? "Stop using paging here" : "Change how pages are fetched";
  if (path.includes("/checks")) {
    if (op === "remove") return "Stop this totals check";
    if (op === "add") return "Add a new totals check";
    return "Point the totals check elsewhere";
  }
  if (path.includes("/auth")) return "Change how we log in";
  if (path.includes("/operations")) return op === "remove" ? "Stop calling this endpoint" : "Change this API call";
  if (path.includes("/entities")) {
    if (last === "compare_to") return "Compare against another total";
    return `Change the ${last.replace(/_/g, " ")} mapping`;
  }
  if (op === "remove") return "Remove this setting";
  if (op === "add") return "Add this setting";
  return "Change this setting";
}

/** Storyline step -> plain headline + one fact, from facts only. */
export function storylineStep1(driftClass: string | null, failedCheck: string | null): { headline: string; fact: string } {
  const headline = driftPlain(driftClass);
  const fact = failedCheck ? checkPlain(failedCheck) : "drift detected";
  return { headline, fact };
}

export function storylineStep2(resolvedRung: number, aiCalls: number): { headline: string; fact: string } {
  if (resolvedRung <= 1) return { headline: "Fixed by a known rule", fact: "no AI needed" };
  if (resolvedRung === 2) return { headline: "Small model ranked it", fact: "no AI model call" };
  return { headline: "Asked the AI model", fact: aiCalls > 0 ? `${aiCalls} call${aiCalls === 1 ? "" : "s"}` : "rules couldn't decide" };
}

export function storylineStep3(
  decision: string,
  hasProposal: boolean,
  proposalLabel: string | null,
): { headline: string; fact: string } {
  if (decision === "climbing") return { headline: "Repairing now", fact: "no action yet" };
  if (decision === "escalate" && hasProposal) {
    const label = (proposalLabel ?? "").trim();
    const headline = label
      ? label.length <= 48
        ? label
        : `${label.slice(0, 45)}…`
      : "Accept the proposal?";
    return { headline, fact: "tested · reversible" };
  }
  if (decision === "escalate") return { headline: "Answer the question", fact: "no patch to ship" };
  if (decision === "approved") return { headline: "Approved", fact: "shipped · reversible" };
  if (decision === "reject") return { headline: "Rejected", fact: "nothing changed" };
  if (decision === "rolled_back") return { headline: "Rolled back", fact: "previous release live" };
  if (decision === "failed") return { headline: "Failed safely", fact: "nothing shipped" };
  return { headline: "Approve the fix", fact: "verified · reversible" };
}
