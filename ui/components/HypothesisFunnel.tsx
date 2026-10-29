"use client";

import { useState } from "react";

export type FunnelStage = {
  name: string;
  count: number;
  ids?: string[];
  families?: string[];
  source?: string;
};

/**
 * Hypothesis funnel (REQ-032 §3, REQ-039): one line per derivation stage
 * ("generated → amount → wire-consistent → distinct"), each expandable
 * to the hypotheses it holds. Stages come from the persisted derivation
 * evidence record (`GET /runs/{id}/evidence`, kind=hypothesis) — never
 * recomputed in the browser. An empty stage list renders an honest empty
 * state, not an invented funnel.
 */
export function HypothesisFunnel({ stages }: { stages: FunnelStage[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!stages.length) {
    return (
      <p data-testid="hypothesis-funnel-empty" className="text-sm text-ink-2">
        No hypothesis derivation was recorded for this repair.
      </p>
    );
  }
  const headline = stages.map((s) => s.count).join(" → ");
  return (
    <div data-testid="hypothesis-funnel">
      <p data-testid="hypothesis-funnel-headline" className="num font-mono text-sm">
        {headline}
      </p>
      <ol className="mt-2 list-none space-y-2 p-0">
        {stages.map((stage, i) => {
          const key = `${stage.name}-${i}`;
          const isOpen = !!open[key];
          const ids = stage.ids ?? [];
          return (
            <li key={key} data-testid={`funnel-stage-${stage.name}`} className="border border-rule px-3 py-2">
              <button
                type="button"
                data-testid={`funnel-stage-toggle-${stage.name}`}
                aria-expanded={isOpen}
                onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}
                className="flex w-full items-center justify-between gap-2 text-left text-sm hover:underline"
              >
                <span>
                  <b className="num">{stage.count}</b> {stage.name}
                  {stage.families?.length ? (
                    <span className="text-ink-2"> · {stage.families.join(", ")}</span>
                  ) : null}
                </span>
                <span aria-hidden="true" className="text-ink-2">
                  {isOpen ? "▾" : "▸"}
                </span>
              </button>
              {isOpen ? (
                <div data-testid={`funnel-stage-detail-${stage.name}`} className="mt-2 text-xs text-ink-2">
                  {stage.source ? <p>{stage.source}</p> : null}
                  {ids.length ? (
                    <ul className="mt-1 list-none space-y-0.5 p-0 font-mono">
                      {ids.map((id) => (
                        <li key={id}>{id}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>No hypothesis ids recorded for this stage.</p>
                  )}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
