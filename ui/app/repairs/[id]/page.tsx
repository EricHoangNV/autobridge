"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { PatchDiff } from "@/components/PatchDiff";
import { ModelCallBox, type LedgerAttempt } from "@/components/ModelCallBox";
import { ModelUsageChart } from "@/components/ModelUsageChart";
import { DriftEvidenceSection } from "@/components/DriftEvidenceSection";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { HypothesesPanel } from "@/components/HypothesesPanel";
import { VerifierVerdict } from "@/components/VerifierVerdict";
import { RepairActions } from "@/components/RepairActions";
import { InjectFaultDrawer } from "@/components/InjectFaultDrawer";
import { WireChangeSummary } from "@/components/WireChangeSummary";
import { HypothesisFunnel, type FunnelStage } from "@/components/HypothesisFunnel";
import { ApprovePreviewSection } from "@/components/ApprovePreview";
import { AfterApproveTracker } from "@/components/AfterApproveTracker";
import type { ModelUsageResponse } from "@/lib/api/client";
import { useRunEvents } from "@/lib/sse";
import { maxLadderLevel } from "@/lib/ladder-events";
import { useNow } from "@/lib/use-now";
import { useFrontierTicker } from "@/lib/use-frontier-ticker";

// Same-origin /api base (REQ-018): the rewrite + middleware forward to control.
import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl();
  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

type RepairOut = {
  id: string;
  drift_event_ids: string[];
  ladder_level_reached: number;
  vendor?: string | null;
  drift_class?: string | null;
  drift_signature?: string | null;
  fingerprint_delta?: Record<string, unknown> | null;
  candidate_patch?: Array<{ op: string; path: string; value?: unknown; from?: string }> | null;
  rule_id?: string | null;
  hypothesis_ids: string[];
  visible_test_results: Array<Record<string, unknown>>;
  verifier_report_id?: string | null;
  decision: string;
  model_usage: Record<string, unknown>;
  ladder_trace: Array<Record<string, unknown>>;
  escalation_bundle?: Record<string, unknown> | null;
  approval?: {
    id: string;
    actor: string;
    decision: string;
    from_release_id?: string | null;
    to_release_id?: string | null;
    created_at: string;
  } | null;
  created_at: string;
};

type DriftOut = {
  id: string;
  connector_id: string;
  drift_class: string;
  severity: string;
  evidence_ids: string[];
  source_sync_run_id?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

type EvidenceOut = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  parent_ids: string[];
  run_id: string;
  created_at: string;
};

type SyncRunPayload = {
  id: string;
  release_id: string;
  started_at: string;
  finished_at?: string | null;
  records_observed: Record<string, number>;
  records_emitted: Record<string, number>;
  outcome: string;
  unmet_criteria: string[];
};

type ReleaseLite = {
  id: string;
  version: number;
  spec: Record<string, unknown>;
};

type ConnectorLite = {
  id: string;
  name: string;
  state?: string | null;
  activity?: string | null;
  target_entities?: string[];
  active_release_id?: string | null;
  releases?: ReleaseLite[] | null;
};

type LedgerOut = {
  repair_run_id: string;
  decision: string;
  resolved_rung: number;
  attempts: LedgerAttempt[];
  total_calls: number;
  small_tokens: number;
  frontier_tokens: number;
  total_cost: string;
  frontier_budget?: number | null;
};

type CapabilitiesOut = {
  rules: { id: string; fixes: string; confidence: number }[];
};

type ModelUsageOut = ModelUsageResponse;

type PageProps = {
  params: Promise<{ id: string }> | { id: string };
};

function toNumber(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

function formatInt(n: number): number | string {
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "0";
}

function humanizeDriftClass(dc: string): string {
  const words = dc.toLowerCase().replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : dc;
}

function shortTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function shortDuration(startedAt: string, finishedAt: string | null | undefined): string | null {
  if (!finishedAt) return null;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  // REQ-048 item 7: a faulted sync can stamp identical start/end times —
  // "took 0 ms" is a clock artefact, not a measurement. Omit it.
  if (!Number.isFinite(ms) || ms <= 0) return null;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** `prefers-reduced-motion: reduce` — the signature motion stays off then. */
function useReducedMotion(): boolean {
  const [reduced] = useState(() => {
    try {
      if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
        return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      }
    } catch {
      // no matchMedia (jsdom default): animate.
    }
    return false;
  });
  return reduced;
}

/** Read a JSON Pointer (RFC 6901) out of the active release spec. */
function specPointer(spec: unknown, pointer: string): { found: boolean; value?: unknown } {
  if (!pointer || pointer === "" || pointer === "/") return { found: true, value: spec };
  if (!pointer.startsWith("/")) return { found: false };
  const parts = pointer
    .split("/")
    .slice(1)
    .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
  let cur: unknown = spec;
  for (const part of parts) {
    if (Array.isArray(cur)) {
      const i = Number(part);
      if (!Number.isInteger(i) || i < 0 || i >= cur.length) return { found: false };
      cur = cur[i];
    } else if (cur !== null && typeof cur === "object") {
      if (!Object.prototype.hasOwnProperty.call(cur, part)) return { found: false };
      cur = (cur as Record<string, unknown>)[part];
    } else {
      return { found: false };
    }
  }
  return { found: true, value: cur };
}

function formatSpecValue(v: unknown): string {
  if (v === undefined) return "unset";
  try {
    const s = JSON.stringify(v);
    return s === undefined ? "unset" : s;
  } catch {
    return String(v);
  }
}

type PatchOp = { op: string; path: string; value?: unknown; from?: string };
type OpRow = { path: string; field: string; now: string; proposed: string };
type OpGroup = { name: string; rows: OpRow[] };

/**
 * Group a JSON Patch by operation (REQ-054 item 1): `Now` is read from the
 * current active spec — never a placeholder. `Proposed` is the patch value.
 */
function groupPatchOps(ops: PatchOp[], activeSpec: Record<string, unknown> | null): OpGroup[] {
  const groups = new Map<string, OpRow[]>();
  for (const op of ops) {
    const path = String(op.path ?? "/");
    const segs = path.split("/").filter(Boolean);
    let name = "spec";
    let field = path;
    if (segs[0] === "operations" && segs[1]) {
      name = segs[1];
      field = `/${segs.slice(2).join("/")}` || "/";
    } else if (segs[0] === "entities" && segs[1]) {
      name = segs[1];
      field = `/${segs.slice(2).join("/")}` || "/";
    }
    let now: string;
    let proposed: string;
    if (op.op === "add") {
      now = "unset";
      proposed = formatSpecValue(op.value);
    } else if (op.op === "remove") {
      const r = activeSpec ? specPointer(activeSpec, path) : { found: false as boolean };
      now = r.found ? formatSpecValue((r as { value?: unknown }).value) : activeSpec ? "unset" : "—";
      proposed = "removed";
    } else if (op.op === "move" || op.op === "copy") {
      const from = typeof op.from === "string" ? op.from : "?";
      const r = activeSpec && typeof op.from === "string" ? specPointer(activeSpec, from) : { found: false as boolean };
      now = r.found ? formatSpecValue((r as { value?: unknown }).value) : activeSpec ? "unset" : "—";
      proposed = `${op.op} → ${path}`;
    } else {
      const r = activeSpec ? specPointer(activeSpec, path) : { found: false as boolean };
      now = r.found ? formatSpecValue((r as { value?: unknown }).value) : activeSpec ? "unset" : "—";
      proposed = formatSpecValue(op.value);
    }
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name)!.push({ path, field, now, proposed });
  }
  return [...groups.entries()].map(([name, rows]) => ({ name, rows }));
}

export default function RepairDetailPage({ params }: PageProps) {
  const [repairId, setRepairId] = useState(() => {
    if (params !== null && typeof params === "object" && "then" in (params as Record<string, unknown>)) {
      return "";
    }
    return decodeURIComponent((params as { id: string }).id);
  });

  useEffect(() => {
    if (params !== null && typeof params === "object" && "then" in (params as Record<string, unknown>)) {
      (params as Promise<{ id: string }>).then((v) => setRepairId(decodeURIComponent(v.id)));
    }
  }, [params]);

  const reducedMotion = useReducedMotion();
  useEffect(() => {
    // Signature motion (REQ-054 item 6): the storyline bars fill 1 → 2 → 3,
    // then Approve pulses once. Reduced motion: bars drawn full, no pulse.
    try {
      if (!reducedMotion) document.documentElement.classList.add("js-story");
      else document.documentElement.classList.remove("js-story");
    } catch {
      // server render: no document.
    }
    return () => {
      try {
        document.documentElement.classList.remove("js-story");
      } catch {
        // ignore
      }
    };
  }, [reducedMotion]);

  const {
    data: repair,
    error: repairError,
    isLoading: repairLoading,
    mutate: mutateRepair,
  } = useSWR<RepairOut>(repairId ? `/repairs/${encodeURIComponent(repairId)}` : null, fetcher, {
    refreshInterval: 2000,
  });

  // Fetch drift events linked to this repair
  const { data: driftEvents } = useSWR<DriftOut[]>(
    repair ? `drift-for-repair-${repair.id}` : null,
    async () => {
      const events: DriftOut[] = [];
      for (const did of repair!.drift_event_ids) {
        try {
          const d = await fetcher<DriftOut>(`/drift/${encodeURIComponent(did)}`);
          events.push(d);
        } catch {
          // drift may have been deleted
        }
      }
      return events;
    },
    { refreshInterval: 2000 },
  );

  // Fetch evidence records linked to drift events (+ hypothesis ids)
  const { data: evidenceRecords } = useSWR<EvidenceOut[]>(
    driftEvents?.length ? `evidence-for-repair-${repair!.id}` : null,
    async () => {
      const records: EvidenceOut[] = [];
      for (const d of driftEvents!) {
        for (const eid of d.evidence_ids) {
          try {
            const e = await fetcher<EvidenceOut>(`/evidence/${encodeURIComponent(eid)}`);
            records.push(e);
          } catch {
            // missing evidence
          }
        }
      }
      for (const hid of repair!.hypothesis_ids) {
        try {
          const e = await fetcher<EvidenceOut>(`/evidence/${encodeURIComponent(hid)}`);
          if (!records.some((r) => r.id === e.id)) records.push(e);
        } catch {
          // hypothesis id may not be an evidence id
        }
      }
      return records;
    },
    { refreshInterval: 2000 },
  );

  // All evidence owned by the repair run (derivation record with stages,
  // verification records) — persisted rows, not recomputed.
  const { data: runEvidence } = useSWR<EvidenceOut[]>(
    repair ? `/runs/${encodeURIComponent(repair.id)}/evidence` : null,
    fetcher,
    { refreshInterval: 2000 },
  );

  // The rung ledger: per-rung action/reason plus every model call.
  const { data: ledger } = useSWR<LedgerOut>(
    repair ? `/repairs/${encodeURIComponent(repair.id)}/ledger` : null,
    fetcher,
    { refreshInterval: 2000 },
  );

  // Connector for the primary drift (name, entities, state, releases — the
  // active release spec supplies the patch table's Now column).
  const primaryConnectorId = driftEvents?.[0]?.connector_id ?? null;
  const { data: connector } = useSWR<ConnectorLite>(
    primaryConnectorId ? `/connectors/${encodeURIComponent(primaryConnectorId)}` : null,
    fetcher,
    { refreshInterval: 2000 },
  );

  // The failing sync that detected the drift (outcome + unmet criterion).
  const sourceSyncId = driftEvents?.[0]?.source_sync_run_id ?? null;
  const { data: sourceSyncRun } = useSWR<{ kind: string; payload: { sync_run: SyncRunPayload } }>(
    sourceSyncId ? `/runs/${encodeURIComponent(sourceSyncId)}` : null,
    fetcher,
    { refreshInterval: 20000 },
  );

  // Rule confidence for "confidence and why".
  const { data: capabilities } = useSWR<CapabilitiesOut>("/capabilities", fetcher);
  const ruleConfidence = capabilities?.rules.find((r) => r.id === repair?.rule_id)?.confidence;

  // Per-run model-usage chart (appendix, chapter 2): persisted ModelCall rows
  // from GET /metrics/model-usage; the current repair run is highlighted.
  const { data: modelUsage, isLoading: modelUsageLoading } = useSWR<ModelUsageOut>(
    repair ? "/metrics/model-usage" : null,
    fetcher,
    { refreshInterval: 10000 },
  );

  const sseRunId = driftEvents?.[0]?.source_sync_run_id ?? null;
  const runEvents = useRunEvents(sseRunId);
  const sseLevel = maxLadderLevel(runEvents);

  const earlyMu = (repair?.model_usage ?? {}) as Record<string, unknown>;
  const earlyFrontier = toNumber(
    earlyMu["frontier_tokens"] ?? earlyMu["frontier_tokens_total"] ?? earlyMu["frontier_tokens_legacy"],
  );
  const earlyLevel = repair?.ladder_level_reached ?? 0;
  const earlyTrack = Math.max(earlyLevel, sseLevel ?? earlyLevel);
  const earlyReport = !!repair?.verifier_report_id;
  const tickingFrontier = useFrontierTicker(earlyFrontier, earlyTrack >= 3 || earlyReport);
  // Ticks the in-flight "calling <model> · Ns" line below; placed with the
  // other hooks (before the loading/error early returns) so hook order is
  // stable across renders.
  const nowMs = useNow(1000);


  if (repairLoading || !repairId) {
    return (
      <div data-testid="repair-detail-page" data-state="loading" aria-busy="true">
        <div className="py-8 text-center text-sm text-ink-2">Loading repair…</div>
      </div>
    );
  }

  if (repairError) {
    return (
      <div data-testid="repair-detail-page" data-state="error">
        <a href="/repairs" data-testid="back-to-repairs" className="text-xs text-ink-2 hover:text-ink hover:underline">
          ← Back to repairs
        </a>
        <div
          data-testid="repair-detail-error"
          className="mt-4 border border-stop px-4 py-3 text-sm"
          style={{ color: "var(--stop)" }}
        >
          Failed to load repair {(repairError as Error).message}
        </div>
      </div>
    );
  }

  if (!repair) {
    return (
      <div data-testid="repair-detail-page" data-state="ready" className="py-8 text-center text-sm text-ink-2">
        Repair not found.
      </div>
    );
  }

  const probes = evidenceRecords?.filter((e) => e.kind === "probe" || e.kind === "observation") ?? null;
  const hypotheses = evidenceRecords?.filter((e) => e.kind === "hypothesis")
    ? evidenceRecords
        .filter((e) => e.kind === "hypothesis")
        .map((e) => ({ id: e.id, ...e.payload, _evidence: e }))
    : null;

  // Funnel stages from the persisted derivation record (engine or demo
  // record-climb), oldest first; the derivation owns the counts.
  const derivation = (runEvidence ?? []).find(
    (e) => e.kind === "hypothesis" && Array.isArray((e.payload as Record<string, unknown>)["stages"]),
  );
  const funnelStages: FunnelStage[] = (() => {
    const raw = (derivation?.payload as Record<string, unknown> | undefined)?.["stages"];
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((s): s is Record<string, unknown> => typeof s === "object" && s !== null)
      .map((s) => ({
        name: String(s["name"] ?? "stage"),
        count: typeof s["count"] === "number" ? s["count"] : 0,
        ids: Array.isArray(s["ids"]) ? s["ids"].map(String) : [],
        families: Array.isArray(s["families"]) ? s["families"].map(String) : [],
        source: typeof s["source"] === "string" ? s["source"] : undefined,
      }));
  })();

  const level = repair.ladder_level_reached ?? 0;
  const isEscalated = repair.decision === "escalate";
  const hasReport = !!repair.verifier_report_id;
  const hasPatch = (repair.candidate_patch ?? []).length > 0;
  // REQ-043: a running repair (decision "climbing") is still climbing by
  // definition; the old propose-without-report shape keeps its meaning.
  const climbingNow = repair.decision === "climbing" || (!hasReport && repair.decision === "propose");
  const trackLevel = Math.max(level, sseLevel ?? level);
  // The rung the climb is on, from the persisted trace (the row's
  // ladder_level_reached stays 0 until the final write).
  const runningRung = Math.max(
    trackLevel,
    ...(repair.ladder_trace ?? []).map((e) => (typeof e["level"] === "number" ? (e["level"] as number) : 0)),
  );
  // In-flight model call, if any: the rung entry stamped with the model id
  // and call start just before the call went out (REQ-043 item 3).
  const inFlightEntry = repair.decision === "climbing"
    ? [...(repair.ladder_trace ?? [])].reverse().find(
        (e) => typeof e["model"] === "string" && typeof e["call_started_at"] === "string",
      )
    : undefined;
  const inFlightElapsed = (() => {
    if (!inFlightEntry) return null;
    const started = Date.parse(String(inFlightEntry["call_started_at"]));
    if (!Number.isFinite(started)) return null;
    return Math.max(0, Math.floor((nowMs - started) / 1000));
  })();
  const pageState = isEscalated ? "escalated" : climbingNow ? "climbing" : "ready";
  const isTerminal =
    repair.decision === "approved" ||
    repair.decision === "reject" ||
    repair.decision === "rolled_back" ||
    repair.decision === "failed";

  const shortId = repair.id.slice(0, 8);
  const primaryDrift = driftEvents?.[0] ?? null;
  const connectorLabel = connector?.name ?? primaryDrift?.connector_id ?? "unknown connector";
  const driftClass = repair.drift_class ?? primaryDrift?.drift_class ?? null;

  const mu = repair.model_usage ?? {};
  const frontierTokens = toNumber(mu["frontier_tokens"] ?? mu["frontier_tokens_total"] ?? mu["frontier_tokens_legacy"]);
  const smallTokens = toNumber(mu["small_tokens"] ?? mu["small_tokens_total"]);
  const totalCalls =
    toNumber(mu["calls"] ?? mu["total_calls"]) || toNumber(mu["frontier_calls"]) + toNumber(mu["small_calls"]);

  // REQ-054 item 4: model cost lives once, in the proof tile. The decided
  // line names the decision only; the rail names the rule, never the tokens.
  // Terminal states read first: a rejected or failed repair is never "ready".
  const decisionLine = repair.decision === "climbing"
    ? `Repairing at L${runningRung}: the engine is still climbing — no Approve or Reject until it decides`
    : isEscalated && repair.approval?.decision === "approve"
      // REQ-049 item 6: an approved proposal is a business decision by
      // the operator — never a "verified fix".
      ? `Business decision by ${repair.approval.actor}`
      : isEscalated
      ? "Needs your decision: the engine is not confident"
      : repair.decision === "reject"
        ? "Rejected"
        : repair.decision === "failed"
          ? "Repair failed: the climb crashed before deciding — the trace names the reason"
          : hasReport && hasPatch
            ? "Fix ready: verified"
            : "Still climbing: the verifier has not run yet";

  const syncPayload = sourceSyncRun?.kind === "sync" ? sourceSyncRun.payload.sync_run : null;
  const syncDuration = syncPayload ? shortDuration(syncPayload.started_at, syncPayload.finished_at) : null;

  // "What the engine decided": rejected proposals from the persisted trace.
  const rejectedProposals = (repair.ladder_trace ?? []).filter((e) => {
    const action = String(e["action"] ?? "");
    return action.includes("reject") || action.includes("fail");
  });
  // REQ-048 item 6: a rung stopped by the token budget is labelled on the
  // page — the reason already names BudgetExhausted in the timeline.
  const budgetExhausted = (repair.ladder_trace ?? []).some((e) =>
    String((e as Record<string, unknown>)["reason"] ?? "").includes("BudgetExhausted"),
  );
  // REQ-044: an explicit model escalation carries the model's own question —
  // label it as the model's instead of the engine's.
  const modelAsked = (repair.ladder_trace ?? []).some(
    (e) => String(e["action"] ?? "") === "model_escalation",
  );
  // REQ-047 item 2: the model's rationale sits beneath its question. Read
  // it from the L3 rung (new rows); older rows carry it in the bundle
  // reason after "model rationale:".
  const modelRationale: string | null = (() => {
    for (const e of repair.ladder_trace ?? []) {
      const rec = e as Record<string, unknown>;
      if (String(rec["action"] ?? "") === "model_escalation" && typeof rec["model_rationale"] === "string" && String(rec["model_rationale"]).trim()) {
        return String(rec["model_rationale"]);
      }
    }
    const reason = String((repair.escalation_bundle as Record<string, unknown> | null)?.["reason"] ?? "");
    const marker = "model rationale:";
    const idx = reason.toLowerCase().indexOf(marker);
    if (idx >= 0) return reason.slice(idx + marker.length).trim() || null;
    return null;
  })();
  // REQ-048 item 1: the escalation question lives once, in the rail.
  // The bundle's question is the model's when it escalated explicitly,
  // else the engine's canned question.
  const escalationQuestion = (() => {
    const q = (repair.escalation_bundle as Record<string, unknown> | null)?.["question"];
    return typeof q === "string" && q.trim() ? q : null;
  })();
  // REQ-049 item 5: the model's proposed change rides the escalation
  // bundle (label/effect/patch) — the rail Approve button and diff read it.
  // REQ-053 item 6: the dry-run result rides with it, shown beside the
  // proposal ("tested on the current records: the checks pass").
  const modelProposal = (() => {
    const raw = (repair.escalation_bundle as Record<string, unknown> | null)?.["proposal"];
    if (!raw || typeof raw !== "object") return null;
    const rec = raw as Record<string, unknown>;
    const patch = Array.isArray(rec["patch"]) ? (rec["patch"] as Array<{ op: string; path: string; value?: unknown; from?: string }>) : [];
    if (patch.length === 0) return null;
    return {
      label: typeof rec["label"] === "string" && rec["label"].trim() ? rec["label"] : "Approve",
      effect: typeof rec["effect"] === "string" ? rec["effect"] : "",
      patch,
      dryRun: typeof rec["dry_run"] === "string" && rec["dry_run"].trim() ? rec["dry_run"] : null,
    };
  })();

  // REQ-053 item 6: the dry-run pass rides the proposing rung (written
  // by the detect-layer gate), shown with the offered candidate patch.
  const candidateDryRun = (() => {
    const entries = (repair.ladder_trace ?? []).filter(
      (e) => typeof e["level"] === "number" && (e["level"] as number) === repair.ladder_level_reached,
    );
    for (let i = entries.length - 1; i >= 0; i--) {
      const note = entries[i]["dry_run"];
      if (typeof note === "string" && note.trim()) return note;
    }
    return null;
  })();
  // REQ-048 item 6: the frontier budget that applies to this repair — the
  // row stamps it at write time; the ledger repeats it.
  const frontierBudget = (() => {
    const fromUsage = toNumber((repair.model_usage as Record<string, unknown>)["frontier_budget"]);
    if (fromUsage > 0) return fromUsage;
    const fromLedger = ledger?.frontier_budget;
    return typeof fromLedger === "number" && Number.isFinite(fromLedger) && fromLedger > 0 ? fromLedger : null;
  })();

  const passedCount = repair.visible_test_results.filter((t) => {
    const v = t as Record<string, unknown>;
    if (v["passed"] === true || v["ok"] === true) return true;
    const s = typeof v["status"] === "string" ? (v["status"] as string).toLowerCase() : "";
    return ["pass", "passed", "ok", "success"].includes(s);
  }).length;
  const totalTests = repair.visible_test_results.length;

  // Touched vs untouched spec sections, from the patch paths themselves.
  const touchedSections = [...new Set((repair.candidate_patch ?? []).map((op) => String(op.path ?? "").split("/").filter(Boolean)[0] || "/"))];

  const attempts = ledger?.attempts ?? [];
  const rungByLevel = new Map(attempts.map((a) => [a.rung, a]));

  // The active release spec: chapter 3's Now column reads from it.
  const activeRelease: ReleaseLite | null = (() => {
    const releases = connector?.releases ?? [];
    if (!releases.length) return null;
    if (connector?.active_release_id) {
      const found = releases.find((r) => r.id === connector.active_release_id);
      if (found) return found;
    }
    return releases[releases.length - 1];
  })();
  const activeSpec = (activeRelease?.spec ?? null) as Record<string, unknown> | null;
  const candidateVersion = activeRelease ? activeRelease.version + 1 : null;
  const opGroups = hasPatch ? groupPatchOps((repair.candidate_patch ?? []) as PatchOp[], activeSpec) : [];
  const opCount = (repair.candidate_patch ?? []).length;

  // Ledger calls: the per-call proof rows. The tile carries the totals, so
  // the box renders only when there are per-call facts (or the climb is
  // still landing them) — never a second "0".
  const ledgerCallCount = attempts.reduce((n, a) => n + (a.calls?.length ?? 0), 0);
  const showLedger = ledgerCallCount > 0 || climbingNow;

  // Hypotheses + probes: one tile when the rule needed no enumeration.
  const probeCount = probes?.length ?? 0;
  const funnelTotal = funnelStages.length ? funnelStages[0].count : 0;
  const showFunnel = funnelStages.length > 0 || climbingNow;
  const showHypotheses = (hypotheses?.length ?? 0) > 0;
  const showProbes = probeCount > 0;

  // Headline: the whole story in one sentence, from facts (REQ-054 item 1).
  // Terminal states read first: a rejected or failed repair is never "fixed".
  const headline = climbingNow
    ? `${connectorLabel} drifted (${driftClass ? humanizeDriftClass(driftClass) : "unknown change"}): the engine is repairing at L${runningRung}.`
    : isEscalated && modelProposal
      ? `${connectorLabel} needs a decision: the model proposes “${modelProposal.label}”.`
      : isEscalated
        ? `${connectorLabel} needs a human decision on ${driftClass ? humanizeDriftClass(driftClass) : "this drift"}.`
        : repair.decision === "approved"
          ? `${connectorLabel} ${driftClass ? humanizeDriftClass(driftClass) : "drifted"}: approved${repair.approval ? ` by ${repair.approval.actor}` : ""}.`
          : repair.decision === "reject"
            ? `${connectorLabel} ${driftClass ? humanizeDriftClass(driftClass) : "drifted"}: the patch was rejected.`
            : repair.decision === "failed"
              ? `${connectorLabel} ${driftClass ? humanizeDriftClass(driftClass) : "drifted"}: the climb failed.`
              : hasReport && hasPatch
                ? `${connectorLabel} ${driftClass ? humanizeDriftClass(driftClass) : "drifted"}: fixed at L${trackLevel}${repair.rule_id ? ` by ${repair.rule_id}` : ""}, verified.`
                : `${connectorLabel} ${driftClass ? humanizeDriftClass(driftClass) : "drifted"}: the climb ${repair.decision}.`;
  const subline = [
    primaryDrift ? `Detected ${shortTime(primaryDrift.created_at)}${sourceSyncId ? ` in sync ${sourceSyncId.slice(0, 8)}` : ""}` : null,
    totalTests > 0 ? `verifier ${passedCount}/${totalTests} checks pass` : null,
    `Approve to ship it as release r${candidateVersion ?? "?"}`,
  ].filter(Boolean).join(" · ");

  // Storyline: 1 happened → 2 did → 3 recommends. The yellow mark sits on
  // the step that needs the operator: step 2 while climbing, step 3 after.
  const storyActive: number = climbingNow || repair.decision === "failed" ? 2 : 3;
  const story2Sub = climbingNow
    ? `Repairing at L${runningRung}`
    : isEscalated
      ? "Escalated to you"
      : repair.decision === "failed"
        ? "The climb failed"
        : `Fixed at L${trackLevel}${repair.rule_id ? ` · ${repair.rule_id}` : ""}`;
  const story3Sub = climbingNow
    ? "Waiting on the climb"
    : isEscalated && modelProposal
      ? `Approve: ${modelProposal.label}`
      : isEscalated
        ? "Answer the question"
        : hasPatch
          ? `Approve release r${candidateVersion ?? "?"} · ${opCount} change${opCount === 1 ? "" : "s"}`
          : repair.decision === "approved"
            ? `Shipped${repair.approval?.to_release_id ? ` ${repair.approval.to_release_id.slice(0, 8)}` : ""}`
            : repair.decision === "reject"
              ? "Rejected"
              : "No patch to ship";

  const story2Meta = climbingNow
    ? inFlightEntry
      ? `calling ${String(inFlightEntry["model"])}`
      : "still climbing"
    : isEscalated
      ? "no verified patch"
      : hasReport
        ? "verifier pass"
        : "verifier pending";
  const story3Meta = climbingNow
    ? "no actions yet"
    : isEscalated && modelProposal
      ? "tested proposal"
      : isEscalated
        ? "answer needed"
        : hasPatch
          ? "verified, shippable"
          : repair.decision;
  const stickySummary = climbingNow
    ? `Repairing at L${runningRung}…`
    : isEscalated
      ? "Needs your decision"
      : hasReport && hasPatch
        ? `Fix verified · Approve r${candidateVersion ?? "?"}…`
        : decisionLine;

  return (
    <div data-testid="repair-detail-page" data-state={pageState}>
      <div className="mb-4 flex items-center gap-3">
        <a
          href="/repairs"
          data-testid="back-to-repairs"
          className="text-xs text-ink-2 hover:text-ink hover:underline"
        >
          ← Back to repairs
        </a>
        <span className="text-xs text-ink-2">·</span>
        <a href="/connectors" className="text-xs text-ink-2 hover:text-ink hover:underline">
          Connectors
        </a>
      </div>

      {/* Headline: the whole story in one sentence, from facts. */}
      <section aria-label="Headline">
        <p className="text-xs text-ink-2">
          Repair {shortId} · {repair.decision}
        </p>
        <h1 data-testid="repair-headline" className="rd-headline">
          {headline}
        </h1>
        <p data-testid="repair-subline" className="mt-1 text-sm text-ink-2">
          {subline}
        </p>
        <ol data-testid="storyline" data-animate={reducedMotion ? "off" : "on"} className="rd-storyline">
          <li data-testid="storyline-1" data-active={storyActive === 1 ? "true" : "false"}>
            <a href="#happened">
              <span className="n">1 · What happened</span>
              <span className="t">{driftClass ? humanizeDriftClass(driftClass) : "Drift detected"}</span>
              <span className="m">{primaryDrift ? `${primaryDrift.severity} severity` : "no drift event linked yet"}</span>
            </a>
          </li>
          <li data-testid="storyline-2" data-active={storyActive === 2 ? "true" : "false"} className={storyActive === 2 ? "rd-now" : undefined}>
            <a href="#did">
              <span className="n">2 · What the engine did</span>
              <span className="t">{story2Sub}</span>
              <span className="m">{story2Meta}</span>
            </a>
          </li>
          <li data-testid="storyline-3" data-active={storyActive === 3 ? "true" : "false"} className={storyActive === 3 ? "rd-now" : undefined}>
            <a href="#recommends">
              <span className="n">3 · Recommendation</span>
              <span className="t">{story3Sub}</span>
              <span className="m">{story3Meta}</span>
            </a>
          </li>
        </ol>
      </section>

      <div className="rd-layout">
        <div className="rd-flow">
          {/* Chapter 1: What happened — facts only, no lede sentences. */}
          <section id="happened" data-testid="repair-section-happened" aria-label="What happened" className="rd-chapter">
            <header>
              <span className="no" aria-hidden="true">1</span>
              <h2>What happened</h2>
            </header>
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-ink-2">Connector and vendor</dt>
                <dd data-testid="happened-connector" className="font-mono">
                  {connectorLabel}
                  {repair.vendor ? <span className="text-ink-2"> · vendor {repair.vendor}</span> : null}
                </dd>
              </div>
              <div>
                <dt className="text-ink-2">Detected</dt>
                <dd data-testid="happened-detected" className="font-mono">
                  {primaryDrift ? (
                    <>
                      {shortTime(primaryDrift.created_at)}
                      {sourceSyncId ? <span className="text-ink-2"> · by sync {sourceSyncId.slice(0, 8)}</span> : null}
                    </>
                  ) : (
                    "no drift event linked yet"
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-ink-2">Sync outcome</dt>
                <dd data-testid="happened-sync-outcome" className="font-mono">
                  {syncPayload ? (
                    <>
                      {syncPayload.outcome}
                      {syncPayload.unmet_criteria.length ? (
                        <span className="text-ink-2"> · unmet: {syncPayload.unmet_criteria.join(", ")}</span>
                      ) : null}
                      {syncDuration ? <span className="text-ink-2"> · took {syncDuration}</span> : null}
                    </>
                  ) : sourceSyncId ? (
                    "sync record unavailable"
                  ) : (
                    "no source sync recorded"
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-ink-2">Drift class and severity</dt>
                <dd data-testid="happened-class" className="font-mono">
                  {driftClass ? humanizeDriftClass(driftClass) : "—"}
                  {primaryDrift ? <span className="text-ink-2"> · severity {primaryDrift.severity}</span> : null}
                </dd>
              </div>
            </dl>
            <h3 className="mb-1 mt-4 text-sm font-semibold">What changed on the wire, before → after</h3>
            <WireChangeSummary driftClass={driftClass} delta={repair.fingerprint_delta as never} />
            <details data-testid="affected-evidence" className="mt-4 border-t border-rule">
              <summary data-testid="affected-evidence-summary" className="cursor-pointer py-3 font-semibold text-ink-2 hover:text-ink">
                Affected records and evidence
              </summary>
              <div className="pb-4">
                <p data-testid="happened-affects" className="font-mono text-xs">
                  {[
                    (connector?.target_entities ?? []).length
                      ? `entities: ${(connector?.target_entities ?? []).join(", ")}`
                      : null,
                    syncPayload
                      ? `records observed: ${Object.entries(syncPayload.records_observed ?? {})
                          .map(([k, v]) => `${k} ${v}`)
                          .join(", ") || "none"}`
                      : null,
                    syncPayload
                      ? `records emitted: ${Object.entries(syncPayload.records_emitted ?? {})
                          .map(([k, v]) => `${k} ${v}`)
                          .join(", ") || "none"}`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "nothing recorded"}
                </p>
                <div className="mt-3">
                  <DriftEvidenceSection
                    driftEvents={driftEvents ?? []}
                    evidenceRecords={evidenceRecords}
                    loading={!driftEvents && !!repair.drift_event_ids.length}
                  />
                </div>
              </div>
            </details>
          </section>

          {/* Chapter 2: What the engine did — ladder, proof tiles, appendices. */}
          <section id="did" data-testid="repair-section-did" aria-label="What the engine did" className="rd-chapter">
            <header>
              <span className="no" aria-hidden="true">2</span>
              <h2>What the engine did</h2>
            </header>
            <ol data-testid="rung-timeline" className="list-none space-y-1 p-0 text-sm">
              {[0, 1, 2, 3, 4].map((rung) => {
                const attempt = rungByLevel.get(rung);
                const held = !climbingNow && trackLevel === rung && (hasReport || isEscalated || isTerminal);
                // REQ-048 item 3: the model's question and rationale live once
                // in the rail — the rungs name the handoff instead of
                // repeating them (full reasons stay in the persisted trace).
                const handoff =
                  attempt?.action === "model_escalation"
                    ? "the model asked for a human decision"
                    : rung === 4 && attempt?.action === "escalate" && modelAsked
                      ? "escalated to you"
                      : null;
                return (
                  <li key={rung} data-testid={`rung-timeline-${rung}`} data-held={held ? "true" : "false"} className={held ? "rung-held" : undefined}>
                    <b className="num">L{rung}</b>{" "}
                    {attempt ? (
                      handoff ? (
                        <span className="text-ink-2">{handoff}</span>
                      ) : (
                        <>
                          {attempt.action} — <span className="text-ink-2">{attempt.reason}</span>
                        </>
                      )
                    ) : (
                      <span className="text-ink-2">Not needed</span>
                    )}
                  </li>
                );
              })}
            </ol>
            {inFlightEntry ? (
              <p data-testid="repair-calling" className="mb-2 mt-3 font-mono text-xs">
                calling {String(inFlightEntry["model"])}
                {inFlightElapsed !== null ? <span className="num text-ink-2"> · {inFlightElapsed} s so far</span> : null}
                <span className="text-ink-2"> — tokens land when the call returns</span>
              </p>
            ) : null}
            <div data-testid="repair-proof" className="rd-proof">
              <div data-testid="proof-model-calls">
                <span className="k">Model calls</span>
                <span className="v num">{formatInt(totalCalls)}</span>
                <span className="s">
                  {frontierBudget !== null
                    ? `${formatInt(frontierTokens + smallTokens)} of ${formatInt(frontierBudget)} budget`
                    : `${formatInt(frontierTokens + smallTokens)} tokens total`}
                  {typeof tickingFrontier === "number" && climbingNow ? ` · ${formatInt(tickingFrontier)} so far` : ""}
                </span>
              </div>
              <div data-testid="proof-verifier">
                <span className="k">Verifier</span>
                <VerifierVerdict
                  verifierReportId={repair.verifier_report_id}
                  visibleTestResults={repair.visible_test_results}
                  escalationBundle={repair.escalation_bundle as never}
                  decision={repair.decision}
                  modelEscalated={modelAsked}
                />
                <span data-testid="verifier-checks-line" className="s num">
                  {totalTests > 0 ? `${passedCount}/${totalTests} checks pass` : "no visible checks recorded"}
                  {repair.verifier_report_id ? ` · report ${repair.verifier_report_id.slice(0, 12)}…` : ""}
                </span>
              </div>
              <div data-testid="proof-hypotheses">
                <span className="k">Hypotheses · probes</span>
                {showFunnel || showHypotheses || showProbes ? (
                  <>
                    <span className="v num">{funnelTotal}{showProbes ? ` · ${probeCount}` : ""}</span>
                    <span className="s">
                      {showFunnel ? "enumerated, then filtered" : "recorded"}
                      {showProbes ? ` · ${probeCount} probe${probeCount === 1 ? "" : "s"} run` : ""}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="v num">0 · 0</span>
                    <span className="s">The rule needed no enumeration</span>
                  </>
                )}
              </div>
            </div>
            {showLedger ? (
              <div className="mt-3">
                <ModelCallBox attempts={attempts} pending={climbingNow} />
              </div>
            ) : null}
            {budgetExhausted ? (
              <p data-testid="repair-budget-exhausted" className="mt-2 font-mono text-xs" style={{ color: "var(--stop)" }}>
                Budget exhausted: a rung stopped when the frontier token budget ran out (see the rung timeline) — the run
                escalated instead of guessing.
              </p>
            ) : null}
            {showFunnel ? (
              <div className="mt-4">
                <HypothesisFunnel stages={funnelStages} pending={climbingNow} />
              </div>
            ) : null}
            {showHypotheses ? (
              <div className="mt-3">
                <HypothesesPanel hypothesisIds={repair.hypothesis_ids} hypotheses={hypotheses as never} probes={probes} />
              </div>
            ) : null}
            {showProbes ? (
              <div className="mt-4">
                <h3 className="mb-1 text-sm font-semibold">Probes run</h3>
                <ul className="list-none space-y-1 p-0 text-sm">
                  {probes!.map((p, idx) => (
                    <li key={p.id} data-testid={`repair-probe-${idx}`} className="font-mono text-xs">
                      {(() => {
                        const pay = p.payload as Record<string, unknown>;
                        if (typeof pay["url"] === "string") return String(pay["url"]);
                        if (typeof pay["path"] === "string") return String(pay["path"]);
                        try {
                          return JSON.stringify(p.payload).slice(0, 120);
                        } catch {
                          return p.kind;
                        }
                      })()}{" "}
                      <span className="text-ink-2">
                        {p.kind} · {p.id.slice(0, 8)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <details data-testid="model-usage-runs" className="mt-4 border-t border-rule">
              <summary className="cursor-pointer py-3 font-semibold text-ink-2 hover:text-ink">
                Model usage across runs
              </summary>
              <div className="pb-4">
                <ModelUsageChart
                  usage={modelUsage ?? null}
                  highlightRunIds={repair ? [repair.id] : []}
                  loading={modelUsageLoading && !modelUsage}
                />
              </div>
            </details>
            <details data-testid="evidence-graph-panel" className="border-t border-rule">
              <summary className="cursor-pointer py-3 font-semibold text-ink-2 hover:text-ink">
                Evidence graph
              </summary>
              <div className="pb-4">
                <EvidenceGraph
                  driftEvents={(driftEvents ?? []).map((d) => ({
                    id: d.id,
                    connector_id: d.connector_id,
                    drift_class: d.drift_class,
                    evidence_ids: d.evidence_ids,
                  }))}
                  evidenceRecords={(evidenceRecords ?? []).map((e) => ({
                    id: e.id,
                    kind: e.kind,
                    parent_ids: e.parent_ids,
                    run_id: e.run_id,
                  }))}
                  loading={!driftEvents && !!repair.drift_event_ids.length}
                />
              </div>
            </details>
          </section>

          {/* Chapter 3: What the engine recommends — Now → Proposed, once. */}
          <section id="recommends" data-testid="repair-section-decided" aria-label="What the engine decided" className="rd-chapter">
            <header>
              <span className="no" aria-hidden="true">3</span>
              <h2>What the engine recommends</h2>
            </header>
            <p data-testid="decided-line" className="text-sm">
              {decisionLine}
              {typeof ruleConfidence === "number" && repair.rule_id ? (
                <span className="text-ink-2"> · rule {repair.rule_id} confidence {ruleConfidence}</span>
              ) : null}
            </p>
            {/* REQ-048 item 3: the escalation question and rationale live once,
                in the rail — the decided section keeps the rejected
                proposals and the patch, never the question. */}
            {rejectedProposals.length ? (
              <div data-testid="decided-rejected" className="mt-2 text-sm">
                <h3 className="mb-1 text-sm font-semibold">Rejected proposals</h3>
                <ul className="list-disc space-y-1 pl-5">
                  {rejectedProposals.map((e, i) => (
                    <li key={i} className="font-mono text-xs">
                      {String(e["action"] ?? "rejected")}: {String(e["reason"] ?? "no reason recorded")}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {hasPatch ? (
              <div data-testid="patch-diff" className="rd-ops">
                {opGroups.map((g, gi) => (
                  <div key={gi} data-testid={`op-group-${gi}`} className="rd-op">
                    <h3>{g.name}</h3>
                    <table>
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Now</th>
                          <th>Proposed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {g.rows.map((row, ri) => {
                          const idx = opGroups.slice(0, gi).reduce((n, gg) => n + gg.rows.length, 0) + ri;
                          return (
                            <tr key={ri} data-testid={`op-row-${idx}`}>
                              <td data-testid={`op-field-${idx}`}>{row.field}</td>
                              <td data-testid={`op-now-${idx}`} className="old">{row.now}</td>
                              <td data-testid={`op-proposed-${idx}`} className="new">{row.proposed}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-ink-2">No patch proposed{isEscalated ? " — nothing verified to apply" : " yet"}.</p>
            )}
            {hasPatch ? (
              <>
                <p data-testid="patch-touches" className="mb-2 mt-3 font-mono text-xs text-ink-2">
                  touches: {touchedSections.join(", ") || "—"} · everything else in the spec stays unchanged
                </p>
                {repair.decision === "propose" && candidateDryRun ? (
                  <p data-testid="patch-dry-run" className="mb-2 font-mono text-xs text-ink-2">
                    {candidateDryRun}
                  </p>
                ) : null}
                <details className="mt-2 border-t border-rule">
                  <summary className="cursor-pointer py-3 text-xs text-ink-2 hover:text-ink hover:underline">
                    Raw JSON Patch · {opCount} operation{opCount === 1 ? "" : "s"}
                  </summary>
                  <div className="pb-4">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <span className="text-xs text-ink-2">RFC 6902{activeRelease ? `, against spec r${activeRelease.version}` : ""}</span>
                      <CopyPatchButton patch={repair.candidate_patch ?? []} />
                    </div>
                    <pre data-testid="patch-raw" className="mt-1 overflow-auto border border-rule bg-ground p-2 font-mono text-xs">
                      {JSON.stringify(repair.candidate_patch, null, 2)}
                    </pre>
                  </div>
                </details>
              </>
            ) : null}
          </section>

          <div className="rd-demo-foot">
            <InjectFaultDrawer onInjected={() => mutateRepair()} />
          </div>
        </div>

        {/* Decision rail: sticky on desktop, after chapter 3 below 1100px. */}
        <aside data-testid="repair-section-approve" id="rail-approve" aria-label="Decision" className="rd-rail" data-animate={reducedMotion ? "off" : "on"}>
          {climbingNow ? (
            <div className="rd-card">
              <p className="k">Repair in progress</p>
              <h2 data-testid="repair-decision-line">Repairing at L{runningRung}…</h2>
              <p data-testid="repair-climbing-status" className="sub">
                {inFlightEntry ? (
                  <>
                    calling {String(inFlightEntry["model"])}
                    {inFlightElapsed !== null ? ` · ${inFlightElapsed} s so far` : ""}
                    {typeof tickingFrontier === "number" ? ` · ${formatInt(tickingFrontier)} tokens so far` : ""}
                  </>
                ) : (
                  <>working through the ladder{runningRung > 0 ? ` · on rung L${runningRung}` : ""}</>
                )}
              </p>
              <p className="sub">No Approve or Reject until the climb decides.</p>
            </div>
          ) : isEscalated ? (
            <div className="rd-card">
              <p className="k">Needs your decision</p>
              <h2 data-testid="repair-decision-line">{decisionLine}</h2>
              {repair.approval ? (
                <p data-testid="repair-outcome" className="sub">
                  {repair.approval.decision === "approve" ? "Approved" : "Rejected"} by {repair.approval.actor} · {shortTime(repair.approval.created_at)}
                  {repair.approval.to_release_id ? ` · release ${repair.approval.to_release_id.slice(0, 8)}` : ""}
                </p>
              ) : null}
              {escalationQuestion ? (
                <div data-testid="escalation-question-top" className="escalation">
                  <p>
                    {modelAsked ? "The model's question for you: " : "The engine's question: "}
                    <b>{escalationQuestion}</b>
                  </p>
                  {modelAsked && modelRationale ? (
                    <p data-testid="escalation-rationale-top" className="mt-1 text-xs text-ink-2">
                      Model rationale: {modelRationale}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {modelProposal ? (
                <div data-testid="escalation-proposal-top" className="proposal">
                  <p className="text-sm">
                    The model&apos;s proposed change: <b>{modelProposal.effect || modelProposal.label}</b>
                  </p>
                  {modelProposal.dryRun ? (
                    <p data-testid="proposal-dry-run" className="mt-1 text-xs text-ink-2">
                      {modelProposal.dryRun}
                    </p>
                  ) : null}
                  <div className="mt-2">
                    <PatchDiff candidatePatch={modelProposal.patch as never} title="Proposed change" />
                  </div>
                </div>
              ) : (
                <p className="sub">
                  To resolve it: edit the spec and build a new release
                  {primaryConnectorId ? (
                    <>
                      {" "}from the{" "}
                      <a className="underline hover:text-ink" href={`/connectors/${encodeURIComponent(primaryConnectorId)}`}>
                        connector page
                      </a>
                    </>
                  ) : null}
                  , or Deny below to close this repair. Roll back stays available below.
                </p>
              )}
              <div id="repair-acts" className="mt-2">
                <RepairActions
                  repairId={repair.id}
                  decision={repair.decision}
                  candidatePatch={repair.candidate_patch as never}
                  patchReady={hasReport && hasPatch}
                  connectorId={primaryConnectorId}
                  proposal={modelProposal}
                  onActionComplete={() => mutateRepair()}
                />
              </div>
              <ApprovePreviewSection repairId={repair.id} />
              <AfterApproveTracker repairId={repair.id} connectorId={primaryConnectorId} />
            </div>
          ) : isTerminal ? (
            <div className="rd-card">
              <p className="k">
                {repair.decision === "approved"
                  ? "Approved ✓"
                  : repair.decision === "rolled_back"
                    ? "Rolled back"
                    : repair.decision === "failed"
                      ? "Failed"
                      : "Rejected"}
              </p>
              <h2 data-testid="repair-decision-line">{decisionLine}</h2>
              <p data-testid="repair-outcome" className="sub">
                {repair.approval
                  ? `${repair.approval.decision === "approve" ? "Approved" : "Rejected"} by ${repair.approval.actor} · ${shortTime(repair.approval.created_at)}${
                      repair.approval.to_release_id ? ` · release ${repair.approval.to_release_id.slice(0, 8)}` : ""
                    }`
                  : "No approval record on this repair."}
              </p>
              <div id="repair-acts" className="mt-2">
                <RepairActions
                  repairId={repair.id}
                  decision={repair.decision}
                  candidatePatch={repair.candidate_patch as never}
                  patchReady={hasReport && hasPatch}
                  connectorId={primaryConnectorId}
                  proposal={modelProposal}
                  onActionComplete={() => mutateRepair()}
                />
              </div>
              <ApprovePreviewSection repairId={repair.id} />
              <AfterApproveTracker repairId={repair.id} connectorId={primaryConnectorId} />
            </div>
          ) : (
            <div className="rd-card">
              <p className="k">Needs your decision</p>
              <p data-testid="repair-decision-line" className="decision">{decisionLine}</p>
              <h2>Approve release r{candidateVersion ?? "?"}</h2>
              <p className="sub">
                {repair.rule_id ? `Rule ${repair.rule_id}` : "Verified patch"}
                {typeof ruleConfidence === "number" && repair.rule_id ? ` · confidence ${ruleConfidence}` : ""}
              </p>
              <div id="repair-acts" className="mt-2">
                <RepairActions
                  repairId={repair.id}
                  decision={repair.decision}
                  candidatePatch={repair.candidate_patch as never}
                  patchReady={hasReport && hasPatch}
                  connectorId={primaryConnectorId}
                  proposal={modelProposal}
                  onActionComplete={() => mutateRepair()}
                />
              </div>
              <ApprovePreviewSection repairId={repair.id} />
              <AfterApproveTracker repairId={repair.id} connectorId={primaryConnectorId} />
            </div>
          )}
        </aside>
      </div>

      {/* Sticky bottom bar below 1100px (CSS): the action stays on screen. */}
      <div className="rd-sticky-act" role="region" aria-label="Decision">
        <p data-testid="repair-sticky-summary">{stickySummary}</p>
        <a className="btn" href="#rail-approve">
          {climbingNow ? `Repairing…` : isEscalated ? "Decide…" : hasPatch ? `Approve r${candidateVersion ?? "?"}…` : "Decision…"}
        </a>
      </div>
    </div>
  );
}

function CopyPatchButton({ patch }: { patch: Array<{ op: string; path: string; value?: unknown; from?: string }> }) {
  const [copied, setCopied] = useState(false);
  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(patch, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard not available
    }
  }
  return (
    <button
      type="button"
      data-testid="patch-copy"
      onClick={handleCopy}
      className="border border-rule px-2 py-1 text-xs text-ink-2 hover:text-ink"
    >
      {copied ? "Copied" : "Copy patch"}
    </button>
  );
}
