"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { LadderTrace } from "@/components/LadderTrace";
import { PatchDiff } from "@/components/PatchDiff";
import { ModelUsageView } from "@/components/ModelUsageView";
import { DriftEvidenceSection } from "@/components/DriftEvidenceSection";
import { HypothesesPanel } from "@/components/HypothesesPanel";
import { VisibleTestsPanel } from "@/components/VisibleTestsPanel";
import { VerifierVerdict } from "@/components/VerifierVerdict";
import { RepairActions } from "@/components/RepairActions";
import { InjectFaultDrawer } from "@/components/InjectFaultDrawer";
import { useRunEvents } from "@/lib/sse";
import { maxLadderLevel } from "@/lib/ladder-events";
import { useFrontierTicker } from "@/lib/use-frontier-ticker";

function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
}

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
  candidate_patch?: Array<{ op: string; path: string; value?: unknown; from?: string }> | null;
  rule_id?: string | null;
  hypothesis_ids: string[];
  visible_test_results: Array<Record<string, unknown>>;
  verifier_report_id?: string | null;
  decision: string;
  model_usage: Record<string, unknown>;
  escalation_bundle?: Record<string, unknown> | null;
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

function formatInt(n: number): string {
  return n.toLocaleString("en-US");
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
    refreshInterval: 5000,
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
    { refreshInterval: 8000 },
  );

  // Fetch evidence records linked to drift events
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
      // Also fetch hypothesis evidence if hypothesis_ids look like evidence ids
      for (const hid of repair!.hypothesis_ids) {
        try {
          const e = await fetcher<EvidenceOut>(`/evidence/${encodeURIComponent(hid)}`);
          // Avoid duplicates
          if (!records.some((r) => r.id === e.id)) records.push(e);
        } catch {
          // hypothesis id may not be an evidence id
        }
      }
      return records;
    },
  );

  // The climb on live SSE: the source sync run's evidence stream carries the
  // ladder events, and the track advances as they arrive — no replay timers
  // (REQ-002 slice 3). Falls back to the polled record when SSE is unavailable.
  const sseRunId = driftEvents?.[0]?.source_sync_run_id ?? null;
  const runEvents = useRunEvents(sseRunId);
  const sseLevel = maxLadderLevel(runEvents);

  // These hooks run before the early returns below, so derive the ticker
  // inputs from the possibly-still-loading repair with safe fallbacks.
  const earlyMu = (repair?.model_usage ?? {}) as Record<string, unknown>;
  const earlyFrontier = toNumber(
    earlyMu["frontier_tokens"] ?? earlyMu["frontier_tokens_total"] ?? earlyMu["frontier_tokens_legacy"],
  );
  const earlyLevel = repair?.ladder_level_reached ?? 0;
  const earlyTrack = Math.max(earlyLevel, sseLevel ?? earlyLevel);
  const earlyReport = !!repair?.verifier_report_id;
  // The frontier counter moves only at L3 (or once the verifier has run).
  const tickingFrontier = useFrontierTicker(earlyFrontier, earlyTrack >= 3 || earlyReport);

  if (repairLoading || !repairId) {
    return (
      <div data-testid="repair-detail-page" data-state="loading" aria-busy="true">
        <div className="repair-banner" role="status">
          <p>Loading repair…</p>
        </div>
        <div className="repair-stage">
          <div className="repair-call">
            <p className="text-sm text-ink-2">Loading repair…</p>
          </div>
          <div className="repair-bug" aria-hidden="true" />
        </div>
      </div>
    );
  }

  if (repairError) {
    return (
      <div data-testid="repair-detail-page" data-state="error">
        <a href="/repairs" data-testid="back-to-repairs" className="text-xs text-ink-2 hover:text-ink hover:underline">
          ← Back to repairs
        </a>
        <div className="repair-banner" role="alert">
          <p>Can&apos;t reach the control API at :8101. Showing the last stored state.</p>
        </div>
        <div
          data-testid="repair-detail-error"
          className="mt-4 border px-4 py-3 text-sm"
          style={{ borderColor: "var(--stop)", color: "var(--stop)" }}
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

  // Derive probes + hypotheses from evidence records
  const probes = evidenceRecords?.filter((e) => e.kind === "probe" || e.kind === "observation") ?? null;
  const hypotheses = evidenceRecords?.filter((e) => e.kind === "hypothesis")
    ? evidenceRecords
        .filter((e) => e.kind === "hypothesis")
        .map((e) => ({ id: e.id, ...e.payload, _evidence: e }))
    : null;

  const level = repair.ladder_level_reached ?? 0;
  const isEscalated = repair.decision === "escalate";
  const hasReport = !!repair.verifier_report_id;
  const climbingNow = !hasReport && repair.decision === "propose";
  // Live rung: the polled record, raised by any newer SSE ladder event.
  const trackLevel = Math.max(level, sseLevel ?? level);
  const pageState = isEscalated ? "escalated" : climbingNow ? "climbing" : "ready";

  const shortId = repair.id.slice(0, 8);
  const primaryDrift = driftEvents?.[0] ?? null;
  const connectorLabel = primaryDrift ? primaryDrift.connector_id : "unknown connector";
  const driftClass = primaryDrift ? primaryDrift.drift_class : null;

  // Rung headlines while the repair is still climbing (the prototype's climb
  // frames). The final headline, the verdict and the action appear only once
  // a patch has passed (REQ-002 slice 3).
  const CLIMB_HEADLINES = [
    "Checking known fixes for this change",
    "Checking the rule registry",
    "Climbing: the small model is ranking hypotheses",
  ];
  const headline = isEscalated
    ? `Needs you at L${trackLevel}`
    : climbingNow && trackLevel <= 2
      ? CLIMB_HEADLINES[trackLevel]
      : climbingNow
        ? "Frontier patch proposed — waiting for the verifier."
        : repair.rule_id && trackLevel <= 1
          ? `Rule ${repair.rule_id} matched at L${trackLevel}`
          : driftClass
            ? `${humanizeDriftClass(driftClass)} at L${trackLevel}`
            : `Repair ${shortId} at L${trackLevel}`;

  const story = (() => {
    const since = primaryDrift ? shortTime(primaryDrift.created_at) : null;
    const n = repair.drift_event_ids.length;
    const sev = primaryDrift ? primaryDrift.severity : null;
    const bits: string[] = [];
    if (since && driftClass) {
      bits.push(
        `Since ${since} ${connectorLabel} shows ${humanizeDriftClass(driftClass).toLowerCase()}${sev ? ` (severity ${sev})` : ""} across ${n} linked drift event${n === 1 ? "" : "s"}.`,
      );
    } else {
      bits.push(
        `Repair ${shortId} links ${n} drift event${n === 1 ? "" : "s"}${sev ? ` at severity ${sev}` : ""}.`,
      );
    }
    if (repair.rule_id && trackLevel <= 1) {
      bits.push(`Rule ${repair.rule_id} repairs it with zero frontier tokens.`);
    } else if (isEscalated) {
      bits.push(`No rule repairs that without evidence, so the ladder stopped at L${trackLevel} and asks you.`);
    } else if (climbingNow) {
      bits.push(`The ladder is still climbing at L${trackLevel}; the verifier has not run yet.`);
    } else if (repair.decision === "reject") {
      bits.push(`The ladder reached L${trackLevel} and the patch was rejected.`);
    } else {
      bits.push(`The ladder reached L${trackLevel} and the verifier checked the candidate patch.`);
    }
    return bits.join(" ");
  })();

  const mu = repair.model_usage ?? {};
  const frontierTokens = toNumber(mu["frontier_tokens"] ?? mu["frontier_tokens_total"] ?? mu["frontier_tokens_legacy"]);
  const smallTokens = toNumber(mu["small_tokens"] ?? mu["small_tokens_total"]);
  const totalCalls =
    toNumber(mu["calls"] ?? mu["total_calls"]) || toNumber(mu["frontier_calls"]) + toNumber(mu["small_calls"]);
  const levelTokens = {
    0: "0 tokens",
    1: "0 tokens",
    2: smallTokens > 0 ? formatInt(smallTokens) : "—",
    3: hasReport || frontierTokens > 0 ? formatInt(frontierTokens) : climbingNow ? "not yet" : "0",
    4: isEscalated ? "you" : hasReport ? "approve" : "not yet",
  } as Partial<Record<0 | 1 | 2 | 3 | 4, string>>;

  const passedCount = repair.visible_test_results.filter((t) => {
    const v = t as Record<string, unknown>;
    if (v["passed"] === true || v["ok"] === true) return true;
    const s = typeof v["status"] === "string" ? (v["status"] as string).toLowerCase() : "";
    return ["pass", "passed", "ok", "success"].includes(s);
  }).length;
  const totalTests = repair.visible_test_results.length;

  const traceTag = primaryDrift ? `${shortTime(primaryDrift.created_at)} · ${primaryDrift.drift_class}` : "no drift linked yet";
  // Marker position: spread markers across the trace when several drifts link here.
  const markerXs = (driftEvents ?? []).map((_, i, arr) => (arr.length === 1 ? 703 : 120 + Math.round((i * 760) / Math.max(1, arr.length - 1))));

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

      <div className="repair-banner" role="status">
        <p>Can&apos;t reach the control API at :8101. Showing the last stored state.</p>
      </div>

      {/* Stage: the call (left) + the scorebug (right) */}
      <section className="repair-stage" aria-label="Repair call and verdict">
        <div className="repair-call">
          <div className="repair-who">
            <b>{connectorLabel}</b>
            <span>·</span>
            <span>
              repair <span className="font-mono">{shortId}</span>
            </span>
            {driftClass ? (
              <>
                <span>·</span>
                <code>{driftClass}</code>
              </>
            ) : null}
          </div>

          <div className="repair-rung">
            <span className={`repair-big${frontierTokens === 0 && trackLevel <= 1 ? " is-zero" : ""}`} aria-hidden="true">
              L{trackLevel}
            </span>
            <h1 data-testid="repair-title" id="repair-headline" className="repair-headline">
              <span className="sr-only">L{trackLevel}: </span>
              {headline}
            </h1>
          </div>

          {isEscalated && repair.escalation_bundle?.["question"] ? (
            <p className="repair-ask" data-testid="repair-escalation-ask">
              The engine&apos;s question: {String(repair.escalation_bundle["question"])}
            </p>
          ) : null}

          <p className="repair-story">{story}</p>
          <p className="sr-only" data-testid="repair-id-short">
            {repair.id}
          </p>
          <p className="sr-only" data-testid="repair-ladder-level">
            L{trackLevel}
          </p>

          <figure
            className="repair-trace"
            aria-label={
              primaryDrift
                ? `Drift trace for ${primaryDrift.drift_class}: ${driftEvents?.length ?? 0} linked event(s), first at ${primaryDrift.created_at}`
                : "Drift trace: no drift events linked yet"
            }
          >
            <span className="tag num">{traceTag}</span>
            <svg viewBox="0 0 1000 132" preserveAspectRatio="none" aria-hidden="true">
              <line className="base" x1="0" y1="40" x2="1000" y2="40" />
              <line className="line" x1="0" y1="40" x2="1000" y2="40" />
              {markerXs.map((x) => (
                <line key={x} className="mark-l" x1={x} y1="18" x2={x} y2="132" />
              ))}
            </svg>
            <figcaption className="num">
              <span>{primaryDrift ? shortTime(primaryDrift.created_at) : "—"}</span>
              <span>Drift evidence, per linked event</span>
              <span>{hasReport ? "candidate patch checked" : pageState === "climbing" ? "climbing…" : "waiting for you"}</span>
            </figcaption>
          </figure>

          <LadderTrace
            ladderLevelReached={trackLevel}
            ruleId={repair.rule_id}
            decision={repair.decision}
            escalationBundle={repair.escalation_bundle as Record<string, unknown> | null}
            levelTokens={levelTokens}
          />
        </div>

        <aside className="repair-bug" aria-label="Verdict and action">
          <VerifierVerdict
            verifierReportId={repair.verifier_report_id}
            visibleTestResults={repair.visible_test_results}
            escalationBundle={repair.escalation_bundle as never}
            decision={repair.decision}
          />
          <ModelUsageView modelUsage={repair.model_usage} liveFrontierTokens={tickingFrontier} pending={climbingNow} />
          <div id="repair-acts">
            <RepairActions
              repairId={repair.id}
              decision={repair.decision}
              candidatePatch={repair.candidate_patch as never}
              patchReady={hasReport && (repair.candidate_patch?.length ?? 0) > 0}
              onActionComplete={() => mutateRepair()}
            />
          </div>
        </aside>
      </section>

      {/* Detail: hypotheses, probes, visible tests */}
      <section className="repair-detail" aria-label="Hypotheses, probes and visible tests">
        <div>
          <h2 className="sec">Hypotheses</h2>
          <HypothesesPanel hypothesisIds={repair.hypothesis_ids} hypotheses={hypotheses as never} probes={probes} />
        </div>
        <div>
          <h2 className="sec">Probes, GET only</h2>
          {probes?.length ? (
            <div>
              {probes.map((p, idx) => {
                let summary = p.kind;
                try {
                  const pay = p.payload as Record<string, unknown>;
                  if (typeof pay["url"] === "string") summary = String(pay["url"]);
                  else if (typeof pay["path"] === "string") summary = String(pay["path"]);
                  else summary = JSON.stringify(p.payload).slice(0, 120);
                } catch {
                  summary = p.kind;
                }
                return (
                  <p key={p.id} className="repair-probe">
                    <span className="pn num">{idx + 1}</span>
                    <code>{summary}</code>
                    <span>
                      {p.kind} · <span className="font-mono">{p.id.slice(0, 8)}</span>
                    </span>
                  </p>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-ink-2">No probe records linked — see Hypotheses for the probe list.</p>
          )}
        </div>
        <div>
          <h2 className="sec">Visible tests</h2>
          <p className="mb-2 text-xs text-ink-2 num">
            {totalTests > 0 ? `${passedCount}/${totalTests} pass` : "No visible tests recorded"}
          </p>
          <VisibleTestsPanel visibleTestResults={repair.visible_test_results as never} />
        </div>
      </section>

      {/* Patch */}
      <section className="repair-patch" aria-label="Patch diff">
        <div className="repair-patch-head">
          <h2 className="sec">Patch, JSON Patch on the spec</h2>
          <p className="num">
            {(repair.candidate_patch ?? []).length} operation{(repair.candidate_patch ?? []).length === 1 ? "" : "s"}
            {hasReport ? " · checked by the verifier" : " · not checked yet"}
          </p>
        </div>
        <h3 className="mb-2 text-sm font-semibold">JSON Patch diff (old → new)</h3>
        <PatchDiff candidatePatch={repair.candidate_patch as never} />
      </section>

      {/* Drift evidence */}
      <section className="mt-6" aria-label="Drift evidence">
        <h2 className="sec">Drift evidence</h2>
        <DriftEvidenceSection
          driftEvents={driftEvents ?? []}
          evidenceRecords={evidenceRecords}
          loading={!driftEvents && !!repair.drift_event_ids.length}
        />
      </section>

      {/* Repair meta (compact, real data) */}
      <section className="mt-6 border border-rule px-4 py-3" aria-label="Repair meta">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs md:grid-cols-4">
          <div>
            <dt className="text-ink-2">Repair ID</dt>
            <dd className="break-all font-mono text-ink-2">{repair.id}</dd>
          </div>
          <div>
            <dt className="text-ink-2">Rule</dt>
            <dd className="font-mono text-ink-2">{repair.rule_id ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-ink-2">Drift events</dt>
            <dd>{repair.drift_event_ids.length} linked</dd>
          </div>
          <div>
            <dt className="text-ink-2">Model calls</dt>
            <dd className="num">{formatInt(totalCalls)}</dd>
          </div>
        </dl>
      </section>

      <div className="mt-6">
        <InjectFaultDrawer onInjected={() => mutateRepair()} />
      </div>

      {/* Sticky phone dock: verdict + approve stay in the first screen */}
      <div className="repair-dock" aria-label="Action">
        <span className="v num">
          {totalTests > 0 ? `${passedCount}/${totalTests}` : hasReport ? "checked" : "climbing"}
          <small>{formatInt(tickingFrontier)} tokens</small>
        </span>
        <a className="btn" href="#repair-acts">
          Approve release
        </a>
      </div>
    </div>
  );
}
