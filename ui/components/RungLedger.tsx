"use client";

import { useEffect, useState } from "react";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

export type RungCall = {
  tier: string;
  purpose: string;
  input_tokens: number;
  output_tokens: number;
  cost: string;
};

export type RungAttempt = {
  rung: number;
  action: string;
  reason: string;
  calls: RungCall[];
};

export type RungLedger = {
  repair_run_id: string;
  decision: string;
  resolved_rung: number;
  attempts: RungAttempt[];
  total_calls: number;
  small_tokens: number;
  frontier_tokens: number;
  total_cost: string;
};

function formatInt(n: number): string {
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "0";
}

export function RungLedger({ repairId }: { repairId: string }) {
  const [ledger, setLedger] = useState<RungLedger | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const base = getBaseUrl().replace(/\/$/, "");
        const res = await fetch(`${base}/repairs/${encodeURIComponent(repairId)}/ledger`, {
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`/repairs/${repairId}/ledger -> ${res.status}`);
        const body = (await res.json()) as RungLedger;
        if (!cancelled) setLedger(body);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repairId]);

  if (error) {
    return (
      <div data-testid="rung-ledger-error" className="text-sm text-ink-2">
        Rung ledger unavailable: {error}
      </div>
    );
  }
  if (!ledger) {
    return (
      <div data-testid="rung-ledger-loading" className="text-sm text-ink-2" aria-busy="true">
        Loading rung ledger…
      </div>
    );
  }
  if (ledger.attempts.length === 0) {
    return (
      <div data-testid="rung-ledger-empty" className="text-sm text-ink-2">
        No rungs recorded for this repair.
      </div>
    );
  }

  return (
    <div data-testid="rung-ledger">
      <ol className="list-none p-0">
        {ledger.attempts.map((attempt, i) => (
          <li
            key={`${attempt.rung}-${attempt.action}-${i}`}
            data-testid={`rung-attempt-L${attempt.rung}`}
            className="border-b border-rule py-3 first:border-t"
          >
            <p className="font-mono text-sm">
              <span className="font-bold text-ink">L{attempt.rung}</span>{" "}
              <span className="text-ink-2">{attempt.action || "—"}</span>
            </p>
            <p className="mt-1 text-[15px] text-ink">{attempt.reason || "No reason recorded."}</p>
            {attempt.calls.length > 0 ? (
              <table className="mt-2 w-full font-mono text-xs" data-testid={`rung-calls-L${attempt.rung}`}>
                <thead>
                  <tr className="text-left text-ink-2">
                    <th className="pr-3 font-normal">tier</th>
                    <th className="pr-3 font-normal">purpose</th>
                    <th className="pr-3 text-right font-normal">tokens</th>
                    <th className="text-right font-normal">cost</th>
                  </tr>
                </thead>
                <tbody>
                  {attempt.calls.map((call, j) => (
                    <tr key={j} className="border-t border-rule">
                      <td className="py-1 pr-3">{call.tier}</td>
                      <td className="py-1 pr-3">{call.purpose || "—"}</td>
                      <td className="py-1 pr-3 text-right">
                        {formatInt(call.input_tokens + call.output_tokens)}
                      </td>
                      <td className="py-1 text-right">${call.cost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="mt-1 font-mono text-xs text-ink-2">0 model calls</p>
            )}
          </li>
        ))}
      </ol>
      <p className="mt-3 font-mono text-xs text-ink-2" data-testid="rung-ledger-totals">
        resolved at L{ledger.resolved_rung} · {ledger.total_calls} calls · {formatInt(ledger.small_tokens)}{" "}
        small / {formatInt(ledger.frontier_tokens)} frontier tokens · ${ledger.total_cost}
      </p>
    </div>
  );
}
