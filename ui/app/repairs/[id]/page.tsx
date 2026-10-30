"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { PatchDiff } from "@/components/PatchDiff";
import { ModelUsageView } from "@/components/ModelUsageView";
import { ModelUsageChart } from "@/components/ModelUsageChart";
import { DriftEvidenceSection } from "@/components/DriftEvidenceSection";
import { EvidenceGraph } from "@/components/EvidenceGraph";
import { HypothesesPanel } from "@/components/HypothesesPanel";
import { VerifierVerdict } from "@/components/VerifierVerdict";
import { RepairActions } from "@/components/RepairActions";
import { InjectFaultDrawer } from "@/components/InjectFaultDrawer";
import { WireChangeSummary } from "@/components/WireChangeSummary";
import { HypothesisFunnel, type FunnelStage } from "@/components/HypothesisFunnel";
import { ModelCallBox, type LedgerAttempt } from "@/components/ModelCallBox";
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

type ConnectorLite = {
  id: string;
  name: string;
  state?: string | null;
  activity?: string | null;
  target_entities?: string[];
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

/** One readable line per JSON Patch op, against the current spec. */
function describePatchOp(op: { op: string; path: string; value?: unknown; from?: string }): string {
  const path = op.path || "/";
  if (op.op === "add") return `add ${path}: ${JSON.stringify(op.value)?.slice(0, 80) ?? ""}`;
  if (op.op === "remove") return `remove ${path}`;
  if (op.op === "move" || op.op === "copy") return `${op.op} ${op.from ?? "?"} → ${path}`;
  if (op.op === "test") return `check ${path} is ${JSON.stringify(op.value)?.slice(0, 80) ?? ""}`;
  if ("value" in op) return `set ${path} to ${JSON.stringify(op.value)?.slice(0, 80) ?? ""}`;
  return `${op.op} ${path}`;
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

  // Connector for the primary drift (name, entities, state).
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

  // Per-run model-usage chart (appendix): persisted ModelCall rows from
  // GET /metrics/model-usage; the current repair run is highlighted.
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

  const shortId = repair.id.slice(0, 8);
  const primaryDrift = driftEvents?.[0] ?? null;
  const connectorLabel = connector?.name ?? primaryDrift?.connector_id ?? "unknown connector";
  const driftClass = repair.drift_class ?? primaryDrift?.drift_class ?? null;

  const mu = repair.model_usage ?? {};
  const frontierTokens = toNumber(mu["frontier_tokens"] ?? mu["frontier_tokens_total"] ?? mu["frontier_tokens_legacy"]);
  const smallTokens = toNumber(mu["small_tokens"] ?? mu["small_tokens_total"]);
  const totalCalls =
    toNumber(mu["calls"] ?? mu["total_calls"]) || toNumber(mu["frontier_calls"]) + toNumber(mu["small_calls"]);

  const decisionLine = repair.decision === "climbing"
    ? `Repairing at L${runningRung}: the engine is still climbing — no Approve or Reject until it decides`
    : isEscalated && repair.approval?.decision === "approve"
      // REQ-049 item 6: an approved proposal is a business decision by
      // the operator — never a "verified fix".
      ? `Business decision by ${repair.approval.actor}`
      : isEscalated
      ? "Needs your decision: the engine is not confident"
      : hasReport && hasPatch
        ? `Fix ready: verified, ${frontierTokens === 0 && smallTokens === 0 ? "0 model calls" : `${formatInt(frontierTokens + smallTokens)} tokens`}`
        : repair.decision === "reject"
          ? "Rejected"
          : repair.decision === "failed"
            ? "Repair failed: the climb crashed before deciding — the trace names the reason"
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
  // REQ-048 item 1: the escalation question lives once, in the top box.
  // The bundle's question is the model's when it escalated explicitly,
  // else the engine's canned question.
  const escalationQuestion = (() => {
    const q = (repair.escalation_bundle as Record<string, unknown> | null)?.["question"];
    return typeof q === "string" && q.trim() ? q : null;
  })();
  // REQ-049 item 5: the model's proposed change rides the escalation
  // bundle (label/effect/patch) — the Approve button and the diff read it.
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
    };
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

      {/* Decision line + actions at the top, one click from the Repairs list */}
      <section aria-label="Decision and actions" className="mb-6 border border-rule bg-band px-4 py-3">
        <p data-testid="repair-decision-line" className="text-base font-semibold">
          {decisionLine}
        </p>
        {isEscalated && escalationQuestion ? (
          <div data-testid="escalation-question-top" className="mt-3 border border-rule bg-ground px-3 py-2.5 text-sm">
            <p>
              {modelAsked ? "The model's question for you: " : "The engine's question: "}
              <b>{escalationQuestion}</b>
            </p>
            {modelAsked && modelRationale ? (
              <p data-testid="escalation-rationale-top" className="mt-1 text-xs text-ink-2">
                Model rationale: {modelRationale}
              </p>
            ) : null}
            <p className="mt-2 text-xs text-ink-2">
              To resolve it: edit the spec and build a new release
              {primaryConnectorId ? (
                <>
                  {" "}from the{" "}
                  <a className="underline hover:text-ink" href={`/connectors/${encodeURIComponent(primaryConnectorId)}`}>
                    connector page
                  </a>
                </>
              ) : null}
              {modelProposal ? null : (
                <>, or Reject below to close this repair</>
              )}
              . Roll back stays available below.
            </p>
            {modelProposal ? (
              <div data-testid="escalation-proposal-top" className="mt-3 border border-rule bg-ground px-3 py-2.5 text-sm">
                <p>
                  The model&apos;s proposed change: <b>{modelProposal.effect || modelProposal.label}</b>
                </p>
                <div className="mt-2">
                  <PatchDiff candidatePatch={modelProposal.patch as never} title="Proposed change" />
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
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
        <AfterApproveTracker repairId={repair.id} connectorId={primaryConnectorId} />
      </section>

      <h1 data-testid="repair-title" className="sr-only">
        Repair {shortId} at L{trackLevel}
      </h1>

      {/* §1 What happened */}
      <section data-testid="repair-section-happened" aria-label="What happened" className="mb-8">
        <h2 className="sec">What happened</h2>
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
        <h3 className="mb-1 mt-4 text-sm font-semibold">What it affects</h3>
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
        <h3 className="mb-1 mt-4 text-sm font-semibold">Evidence records</h3>
        <DriftEvidenceSection
          driftEvents={driftEvents ?? []}
          evidenceRecords={evidenceRecords}
          loading={!driftEvents && !!repair.drift_event_ids.length}
        />
      </section>

      {/* §2 What the engine did */}
      <section data-testid="repair-section-did" aria-label="What the engine did" className="mb-8">
        <h2 className="sec">What the engine did</h2>
        <h3 className="mb-1 text-sm font-semibold">Rung timeline</h3>
        <ol data-testid="rung-timeline" className="list-none space-y-1 p-0 text-sm">
          {[0, 1, 2, 3, 4].map((rung) => {
            const attempt = rungByLevel.get(rung);
            // REQ-048 item 3: the model's question and rationale live once
            // in the top box — the rungs name the handoff instead of
            // repeating them (full reasons stay in the persisted trace).
            const handoff =
              attempt?.action === "model_escalation"
                ? "the model asked for a human decision"
                : rung === 4 && attempt?.action === "escalate" && modelAsked
                  ? "escalated to you"
                  : null;
            return (
              <li key={rung} data-testid={`rung-timeline-${rung}`}>
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
                  <span className="text-ink-2">no attempt recorded</span>
                )}
              </li>
            );
          })}
        </ol>
        <h3 className="mb-1 mt-4 text-sm font-semibold">Hypothesis funnel</h3>
        <HypothesisFunnel stages={funnelStages} pending={climbingNow} />
        {hypotheses?.length ? (
          <div className="mt-3">
            <HypothesesPanel hypothesisIds={repair.hypothesis_ids} hypotheses={hypotheses as never} probes={probes} />
          </div>
        ) : null}
        <h3 className="mb-1 mt-4 text-sm font-semibold">Probes run</h3>
        {probes?.length ? (
          <ul className="list-none space-y-1 p-0 text-sm">
            {probes.map((p, idx) => (
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
        ) : (
          <p className="text-sm text-ink-2">No probe records linked.</p>
        )}
        <h3 className="mb-1 mt-4 text-sm font-semibold">Model calls</h3>
        {inFlightEntry ? (
          <p data-testid="repair-calling" className="mb-2 font-mono text-xs">
            calling {String(inFlightEntry["model"])}
            {inFlightElapsed !== null ? <span className="num text-ink-2"> · {inFlightElapsed} s so far</span> : null}
            <span className="text-ink-2"> — tokens land when the call returns</span>
          </p>
        ) : null}
        <ModelCallBox attempts={attempts} pending={climbingNow} />
        <div className="mt-2">
          <ModelUsageView modelUsage={repair.model_usage} liveFrontierTokens={tickingFrontier} pending={climbingNow} budget={frontierBudget} />
        </div>
        <h3 className="mb-1 mt-4 text-sm font-semibold">Verifier run</h3>
        <VerifierVerdict
          verifierReportId={repair.verifier_report_id}
          visibleTestResults={repair.visible_test_results}
          escalationBundle={repair.escalation_bundle as never}
          decision={repair.decision}
          modelEscalated={modelAsked}
        />
        <p data-testid="verifier-checks-line" className="num mt-1 font-mono text-xs text-ink-2">
          {totalTests > 0 ? `${passedCount}/${totalTests} checks pass` : "no visible checks recorded"}
          {repair.verifier_report_id ? ` · report ${repair.verifier_report_id.slice(0, 12)}…` : ""}
        </p>
      </section>

      {/* §3 What the engine decided and recommends */}
      <section data-testid="repair-section-decided" aria-label="What the engine decided" className="mb-8">
        <h2 className="sec">What the engine decided and recommends</h2>
        <p data-testid="decided-line" className="text-sm">
          {decisionLine}
          {typeof ruleConfidence === "number" && repair.rule_id ? (
            <span className="text-ink-2"> · rule {repair.rule_id} confidence {ruleConfidence}</span>
          ) : null}
        </p>
        {budgetExhausted ? (
          <p data-testid="repair-budget-exhausted" className="mt-1 font-mono text-xs" style={{ color: "var(--stop)" }}>
            Budget exhausted: a rung stopped when the frontier token budget ran out (see the rung timeline) — the run
            escalated instead of guessing.
          </p>
        ) : null}
        {/* REQ-048 item 3: the escalation question and rationale live once,
            in the top box above — the decided section keeps the rejected
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
        <h3 className="mb-1 mt-4 text-sm font-semibold">Proposed change, against the current spec</h3>
        {hasPatch ? (
          <>
            <ul className="mb-2 list-disc space-y-1 pl-5 text-sm">
              {(repair.candidate_patch ?? []).map((op, i) => (
                <li key={i} data-testid={`patch-line-${i}`} className="font-mono text-xs">
                  {describePatchOp(op)}
                </li>
              ))}
            </ul>
            <p data-testid="patch-touches" className="mb-2 font-mono text-xs text-ink-2">
              touches: {touchedSections.join(", ") || "—"} · everything else in the spec stays unchanged
            </p>
            <PatchDiff candidatePatch={repair.candidate_patch as never} />
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-ink-2 hover:text-ink hover:underline">
                Raw JSON Patch
              </summary>
              <pre data-testid="patch-raw" className="mt-1 overflow-auto border border-rule bg-ground p-2 font-mono text-[11px]">
                {JSON.stringify(repair.candidate_patch, null, 2)}
              </pre>
            </details>
          </>
        ) : (
          <p className="text-sm text-ink-2">No patch proposed{isEscalated ? " — nothing verified to apply" : " yet"}.</p>
        )}
      </section>

      {/* §4 What happens when you click Approve */}
      <section data-testid="repair-section-approve" aria-label="What happens when you click Approve" className="mb-8">
        <h2 className="sec">What happens when you click Approve</h2>
        <ApprovePreviewSection repairId={repair.id} />
      </section>

      {/* Evidence graph + usage (appendix, persisted rows) */}
      <section className="mt-6" aria-label="Evidence graph">
        <h2 className="sec">Evidence graph</h2>
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
      </section>

      {/* Model usage per run (appendix, persisted ModelCall rows) */}
      <section className="mt-6" aria-label="Model usage per run">
        <h2 className="sec">Model usage per run</h2>
        <p className="mb-2 text-xs text-ink-2">
          Persisted model calls per repair run (frontier vs small tokens); the current repair is highlighted.
        </p>
        <ModelUsageChart
          usage={modelUsage ?? null}
          highlightRunIds={repair ? [repair.id] : []}
          loading={modelUsageLoading && !modelUsage}
        />
      </section>

      <div className="mt-6">
        <InjectFaultDrawer onInjected={() => mutateRepair()} />
      </div>
    </div>
  );
}
