"use client";

import { useState } from "react";

type HypothesisEntry = {
  id: string;
  family?: string;
  claim?: string;
  field?: string;
  strategy?: string;
  prior?: number;
  evidence_for?: unknown;
  candidate_mapping_fragment?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  [key: string]: unknown;
};

type ProbeEntry = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  parent_ids: string[];
  run_id: string;
  created_at: string;
};

type Props = {
  hypothesisIds: string[];
  hypotheses?: HypothesisEntry[] | null;
  probes?: ProbeEntry[] | null;
  loading?: boolean;
};

export function HypothesesPanel({ hypothesisIds, hypotheses, probes, loading }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (loading) {
    return (
      <div data-testid="hypotheses-loading" className="border bg-band px-4 py-6 text-center text-sm text-ink-2">
        Loading hypotheses…
      </div>
    );
  }

  const hasIds = hypothesisIds.length > 0;
  const hasResolved = hypotheses && hypotheses.length > 0;
  const hasProbes = probes && probes.length > 0;

  if (!hasIds && !hasResolved && !hasProbes) {
    return (
      <div data-testid="hypotheses-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
        No hypotheses — repair was deterministic (L0/L1) or escalated without hypothesis enumeration.
      </div>
    );
  }

  // Resolve display list: prefer fetched hypotheses, fallback to ids
  const displayHyps: HypothesisEntry[] = (() => {
    if (hasResolved) return hypotheses!;
    return hypothesisIds.map((id) => ({ id }));
  })();

  return (
    <div data-testid="hypotheses-panel" className="space-y-4">
      {/* Hypothesis ids / cards */}
      {displayHyps.length ? (
        <div className="overflow-hidden border bg-band">
          <div className="border-b bg-ground px-4 py-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">
              Hypotheses ({displayHyps.length})
            </h3>
          </div>
          <ul className="divide-y divide-rule">
            {displayHyps.map((h) => {
              const isExpanded = expandedId === h.id;
              return (
                <li key={h.id} data-testid={`hypothesis-${h.id}`} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-medium text-ink">{h.id}</span>
                        {h.family ? (
                          <span data-testid={`hypothesis-family-${h.id}`} className="inline-flex bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-800">
                            {h.family}
                          </span>
                        ) : null}
                        {h.strategy ? (
                          <span className="inline-flex bg-ground px-1.5 py-0.5 text-xs text-ink-2">{h.strategy}</span>
                        ) : null}
                        {typeof h.prior === "number" ? (
                          <span className="text-xs text-ink-2">prior {h.prior.toFixed(2)}</span>
                        ) : null}
                      </div>
                      {h.claim ? (
                        <p data-testid={`hypothesis-claim-${h.id}`} className="mt-1 text-xs text-ink-2">
                          {h.claim}
                        </p>
                      ) : null}
                      {h.field ? (
                        <p className="mt-1 font-mono text-xs text-ink-2">field: {h.field}</p>
                      ) : null}
                    </div>
                    {h.candidate_mapping_fragment || h.payload ? (
                      <button
                        data-testid={`hypothesis-toggle-${h.id}`}
                        onClick={() => setExpandedId(isExpanded ? null : h.id)}
                        className="shrink-0 border bg-band px-2 py-1 text-xs text-ink-2 hover:bg-ground"
                      >
                        {isExpanded ? "Hide" : "Details"}
                      </button>
                    ) : null}
                  </div>
                  {isExpanded ? (
                    <pre
                      data-testid={`hypothesis-detail-${h.id}`}
                      className="mt-2 max-h-48 overflow-auto bg-ground p-3 font-mono text-xs text-ink-2"
                    >
                      {JSON.stringify(h.candidate_mapping_fragment ?? h.payload ?? h, null, 2)}
                    </pre>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <div data-testid="hypothesis-ids-list" className="border bg-band px-4 py-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">Hypothesis IDs</h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {hypothesisIds.map((hid) => (
              <span
                key={hid}
                data-testid={`hypothesis-id-${hid}`}
                className="inline-flex bg-ground px-2 py-1 font-mono text-xs text-ink-2"
              >
                {hid}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Probes */}
      {hasProbes ? (
        <div data-testid="probes-section" className="overflow-hidden border bg-band">
          <div className="border-b bg-ground px-4 py-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">Probes ({probes!.length})</h3>
          </div>
          <ul className="divide-y divide-rule">
            {probes!.map((p) => (
              <li key={p.id} data-testid={`probe-${p.id}`} className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="inline-flex bg-sky-50 px-1.5 py-0.5 text-xs font-medium text-sky-700">{p.kind}</span>
                  <span className="font-mono text-xs text-ink-2">{p.id.slice(0, 8)}</span>
                  <span className="ml-auto text-xs text-ink-2">{p.run_id.slice(0, 8)}</span>
                </div>
                <pre className="mt-2 max-h-40 overflow-auto bg-ground p-2 font-mono text-xs text-ink-2">
                  {JSON.stringify(p.payload, null, 2)}
                </pre>
              </li>
            ))}
          </ul>
        </div>
      ) : displayHyps.length > 0 ? (
        <p data-testid="probes-empty" className="text-xs text-ink-2">
          No probe records linked — hypotheses were enumerated without additional probing.
        </p>
      ) : null}
    </div>
  );
}
