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

  if (repairLoading || !repairId) {
    return (
      <div data-testid="repair-detail-page" className="py-8 text-center text-sm text-ink-2">
        Loading repair…
      </div>
    );
  }

  if (repairError) {
    return (
      <div data-testid="repair-detail-page">
        <a href="/repairs" data-testid="back-to-repairs" className="text-xs text-ink-2 hover:text-ink hover:underline">
          ← Back to repairs
        </a>
        <div
          data-testid="repair-detail-error"
          className="mt-4 border border-stop bg-band px-4 py-3 text-sm text-stop"
        >
          Failed to load repair {(repairError as Error).message}
        </div>
      </div>
    );
  }

  if (!repair) {
    return (
      <div data-testid="repair-detail-page" className="py-8 text-center text-sm text-ink-2">
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

  return (
    <div data-testid="repair-detail-page">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3">
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
        <div className="mt-2 flex items-start justify-between gap-4">
          <div>
            <h1 data-testid="repair-title" className="text-xl font-semibold tracking-tight">
              Repair {repair.id.slice(0, 8)}
            </h1>
            <p className="mt-1 font-mono text-xs text-ink-2">{repair.id}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span
                data-testid="repair-decision-badge"
                className={`inline-flex border px-2.5 py-0.5 text-xs font-medium ${
                  repair.decision === "propose"
                    ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                    : repair.decision === "escalate"
                      ? "bg-band text-stop border-stop"
                      : "bg-ground text-ink-2 border-rule"
                }`}
              >
                {repair.decision}
              </span>
              <span data-testid="repair-id-short" className="font-mono text-xs text-ink-2">
                {repair.created_at ? new Date(repair.created_at).toLocaleString() : ""}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Two-column layout: main content + sidebar */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Main column */}
        <div className="space-y-6 lg:col-span-2">
          {/* Drift evidence */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">Drift evidence</h2>
            <DriftEvidenceSection driftEvents={driftEvents ?? []} evidenceRecords={evidenceRecords} loading={!driftEvents && !!repair.drift_event_ids.length} />
          </section>

          {/* Ladder trace */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">Ladder trace</h2>
            <LadderTrace
              ladderLevelReached={repair.ladder_level_reached}
              ruleId={repair.rule_id}
              decision={repair.decision}
              escalationBundle={repair.escalation_bundle as Record<string, unknown> | null}
            />
          </section>

          {/* Hypotheses + probes */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">Hypotheses + probes</h2>
            <HypothesesPanel hypothesisIds={repair.hypothesis_ids} hypotheses={hypotheses as never} probes={probes} />
          </section>

          {/* JSON Patch diff */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">JSON Patch diff (old → new)</h2>
            <PatchDiff candidatePatch={repair.candidate_patch as never} />
          </section>

          {/* Visible tests */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">Visible tests</h2>
            <VisibleTestsPanel visibleTestResults={repair.visible_test_results as never} />
          </section>

          {/* Verifier verdict */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">Verifier verdict</h2>
            <VerifierVerdict
              verifierReportId={repair.verifier_report_id}
              visibleTestResults={repair.visible_test_results}
              escalationBundle={repair.escalation_bundle as never}
              decision={repair.decision}
            />
          </section>

          {/* Model usage */}
          <section>
            <h2 className="mb-2 text-sm font-semibold">Model usage</h2>
            <ModelUsageView modelUsage={repair.model_usage} />
          </section>
        </div>

        {/* Sidebar column */}
        <div className="space-y-6">
          {/* Actions */}
          <RepairActions repairId={repair.id} decision={repair.decision} candidatePatch={repair.candidate_patch as never} onActionComplete={() => mutateRepair()} />

          {/* Model usage compact */}
          <ModelUsageView modelUsage={repair.model_usage} title="Model usage (compact)" compact />

          {/* Inject Fault drawer */}
          <InjectFaultDrawer onInjected={() => mutateRepair()} />

          {/* Repair meta */}
          <div className="border bg-ground px-4 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">Repair meta</h3>
            <dl className="mt-2 space-y-2 text-xs">
              <div>
                <dt className="text-ink-2">Repair ID</dt>
                <dd className="font-mono text-ink-2 break-all">{repair.id}</dd>
              </div>
              <div>
                <dt className="text-ink-2">Ladder level</dt>
                <dd data-testid="repair-ladder-level">L{repair.ladder_level_reached}</dd>
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
                <dt className="text-ink-2">Hypotheses</dt>
                <dd>{repair.hypothesis_ids.length} ids</dd>
              </div>
              <div>
                <dt className="text-ink-2">Verifier report</dt>
                <dd className="font-mono text-ink-2 break-all">{repair.verifier_report_id ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-ink-2">Created</dt>
                <dd>{repair.created_at ? new Date(repair.created_at).toLocaleString() : "—"}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}
