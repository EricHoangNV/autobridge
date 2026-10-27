"use client";

import { useState } from "react";

type HypothesisEntry = {
  id: string;
  family?: string;
  claim?: string;
  field?: string;
  strategy?: string;
  prior?: number;
  confidence?: number;
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

function confidenceOf(h: HypothesisEntry): number | null {
  const raw =
    typeof h.confidence === "number"
      ? h.confidence
      : typeof h.prior === "number"
        ? h.prior
        : typeof h.payload?.["confidence"] === "number"
          ? (h.payload["confidence"] as number)
          : null;
  if (raw == null || !Number.isFinite(raw)) return null;
  return Math.max(0, Math.min(1, raw));
}

function titleOf(h: HypothesisEntry): string {
  if (typeof h.claim === "string" && h.claim) return h.claim;
  const p = h.payload as Record<string, unknown> | undefined;
  const hp = p && typeof p["hypothesis"] === "string" ? (p["hypothesis"] as string) : null;
  if (hp) return hp;
  if (typeof h.family === "string" && h.family) return h.family;
  return h.id;
}

function probeSummary(payload: Record<string, unknown>): string {
  for (const key of ["url", "path", "endpoint", "method", "summary", "observation"]) {
    const v = payload[key];
    if (typeof v === "string" && v) {
      if (key === "method" && typeof payload["url"] === "string") return `${v} ${payload["url"]}`;
      if (key === "method" && typeof payload["path"] === "string") return `${v} ${payload["path"]}`;
      return v;
    }
  }
  try {
    return JSON.stringify(payload).slice(0, 160);
  } catch {
    return "probe record";
  }
}

export function HypothesesPanel({ hypothesisIds, hypotheses, probes, loading }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (loading) {
    return (
      <div data-testid="hypotheses-loading" className="border border-rule px-4 py-6 text-center text-sm text-ink-2">
        Loading hypotheses…
      </div>
    );
  }

  const hasIds = hypothesisIds.length > 0;
  const hasResolved = !!hypotheses && hypotheses.length > 0;
  const hasProbes = !!probes && probes.length > 0;

  if (!hasIds && !hasResolved && !hasProbes) {
    return (
      <div data-testid="hypotheses-empty" className="border border-dashed border-rule px-4 py-6 text-center text-sm text-ink-2">
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
        <div>
          {displayHyps.map((h) => {
            const isExpanded = expandedId === h.id;
            const conf = confidenceOf(h);
            const pct = conf == null ? null : Math.round(conf * 100);
            return (
              <div
                key={h.id}
                data-testid={`hypothesis-${h.id}`}
                className={`repair-hyp${pct != null && pct >= 50 ? " top" : ""}`}
              >
                <span>{titleOf(h)}</span>
                <span className="p num">{pct != null ? (pct / 100).toFixed(2) : "—"}</span>
                {pct != null ? (
                  <span className="bar2" aria-hidden="true">
                    <span style={{ width: `${pct}%` }} />
                  </span>
                ) : null}
                <small>
                  <span className="font-mono">{h.id}</span>
                  {h.family ? (
                    <span data-testid={`hypothesis-family-${h.id}`}> · {h.family}</span>
                  ) : null}
                  {h.strategy ? <span> · {h.strategy}</span> : null}
                  {typeof h.prior === "number" ? <span> · prior {h.prior.toFixed(2)}</span> : null}
                  {h.claim && titleOf(h) !== h.claim ? (
                    <span data-testid={`hypothesis-claim-${h.id}`}> · {h.claim}</span>
                  ) : h.claim ? (
                    <span data-testid={`hypothesis-claim-${h.id}`} className="sr-only">
                      {h.claim}
                    </span>
                  ) : null}
                  {h.field ? <span> · field: {h.field}</span> : null}
                  {h.candidate_mapping_fragment || h.payload ? (
                    <span>
                      {" "}
                      <button
                        data-testid={`hypothesis-toggle-${h.id}`}
                        onClick={() => setExpandedId(isExpanded ? null : h.id)}
                        className="link"
                      >
                        {isExpanded ? "Hide" : "Details"}
                      </button>
                    </span>
                  ) : null}
                </small>
                {isExpanded ? (
                  <pre
                    data-testid={`hypothesis-detail-${h.id}`}
                    className="mt-2 max-h-48 overflow-auto bg-band p-3 font-mono text-xs text-ink-2"
                    style={{ gridColumn: "1 / -1" }}
                  >
                    {JSON.stringify(h.candidate_mapping_fragment ?? h.payload ?? h, null, 2)}
                  </pre>
                ) : null}
              </div>
            );
          })}
          <div data-testid="hypothesis-ids-list" className="sr-only">
            {hypothesisIds.map((hid) => (
              <span key={hid} data-testid={`hypothesis-id-${hid}`}>
                {hid}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div data-testid="hypothesis-ids-list" className="border border-rule px-4 py-3">
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
        <div data-testid="probes-section">
          {probes!.map((p, idx) => (
            <p key={p.id} data-testid={`probe-${p.id}`} className="repair-probe">
              <span className="pn num">{idx + 1}</span>
              <code>{probeSummary(p.payload) || p.kind}</code>
              <span>
                {p.kind} · <span className="font-mono">{p.id.slice(0, 8)}</span>
              </span>
            </p>
          ))}
        </div>
      ) : displayHyps.length > 0 ? (
        <p data-testid="probes-empty" className="text-xs text-ink-2">
          No probe records linked — hypotheses were enumerated without additional probing.
        </p>
      ) : null}
    </div>
  );
}
