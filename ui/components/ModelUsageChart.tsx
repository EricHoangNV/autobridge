"use client";

type ByRunEntry = {
  run_id?: string;
  purpose?: string;
  tokens?: unknown;
  [key: string]: unknown;
};

type UsageEntry = {
  provider: string;
  model: string;
  tier: string;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost: string;
  by_run?: ByRunEntry[] | null;
  [key: string]: unknown;
};

type UsageResponse = {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost: string;
  frontier_tokens: number;
  small_tokens: number;
  by_tier: Record<string, number>;
  by_provider: Record<string, number>;
  entries?: UsageEntry[] | null;
  by_purpose?: Record<string, number> | null;
  [key: string]: unknown;
};

type Props = {
  usage: UsageResponse | null | undefined;
  /** Run ids related to the current repair — highlighted in the chart. */
  highlightRunIds?: string[] | null;
  /** Max rows rendered (highest-token runs first). Default 12. */
  maxRuns?: number;
  loading?: boolean;
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
  return Math.round(n).toLocaleString("en-US");
}

type RunRow = {
  runId: string;
  frontier: number;
  small: number;
  purposes: string[];
};

function aggregatePerRun(usage: UsageResponse): RunRow[] {
  const rows = new Map<string, RunRow>();
  for (const entry of usage.entries ?? []) {
    const tier = (entry.tier || "").toLowerCase();
    const isFrontier = tier === "frontier";
    for (const b of entry.by_run ?? []) {
      const runId = typeof b.run_id === "string" && b.run_id ? b.run_id : "unknown";
      const tokens = toNumber(b.tokens);
      let row = rows.get(runId);
      if (!row) {
        row = { runId, frontier: 0, small: 0, purposes: [] };
        rows.set(runId, row);
      }
      if (isFrontier) row.frontier += tokens;
      else row.small += tokens;
      if (typeof b.purpose === "string" && b.purpose && !row.purposes.includes(b.purpose)) {
        row.purposes.push(b.purpose);
      }
    }
  }
  return [...rows.values()]
    .filter((r) => r.frontier + r.small > 0)
    .sort((a, b) => b.frontier + b.small - (a.frontier + a.small));
}

/**
 * P2.S6.1 — per-run model-usage chart (read-only, no new API surface).
 *
 * Stacked horizontal bars of frontier vs small-model tokens for each run in
 * `GET /metrics/model-usage` (`entries[].by_run`), so a repair page can show
 * where tokens went without leaving the UI. Pure divs, no animation, no
 * chart dependency.
 */
export function ModelUsageChart({ usage, highlightRunIds, maxRuns = 12, loading }: Props) {
  if (loading) {
    return (
      <div data-testid="model-usage-chart-loading" className="border border-dashed px-4 py-4 text-center text-sm text-ink-2">
        Loading model usage…
      </div>
    );
  }

  if (!usage || !usage.entries?.length) {
    return (
      <div data-testid="model-usage-chart-empty" className="border border-dashed border-rule px-4 py-4 text-center text-sm text-ink-2">
        No per-run model usage recorded.
      </div>
    );
  }

  const rows = aggregatePerRun(usage);
  if (!rows.length) {
    return (
      <div data-testid="model-usage-chart-empty" className="border border-dashed border-rule px-4 py-4 text-center text-sm text-ink-2">
        No per-run token data in this usage response.
      </div>
    );
  }

  const visible = rows.slice(0, Math.max(1, maxRuns));
  const hidden = rows.length - visible.length;
  const maxTotal = Math.max(...visible.map((r) => r.frontier + r.small), 1);
  const highlighted = new Set(highlightRunIds ?? []);

  return (
    <div data-testid="model-usage-chart" className="space-y-2">
      <div className="flex items-center gap-4 text-[11px] text-ink-2" aria-hidden="true">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2 w-2 bg-live" /> Frontier
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2 w-2 bg-ink-2" /> Small
        </span>
        <span className="ml-auto num">
          {rows.length} run{rows.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="space-y-1.5">
        {visible.map((row) => {
          const total = row.frontier + row.small;
          const frontierPct = (row.frontier / maxTotal) * 100;
          const smallPct = (row.small / maxTotal) * 100;
          const isHighlight = highlighted.has(row.runId);
          return (
            <li
              key={row.runId}
              data-testid="model-usage-chart-row"
              data-run-id={row.runId}
              data-highlight={isHighlight ? "true" : "false"}
              className={isHighlight ? "bg-band px-2 py-1" : "px-2 py-1"}
            >
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className="truncate font-mono text-ink" title={row.runId}>
                  {isHighlight ? "● " : ""}
                  {row.runId.slice(0, 12)}
                  {row.purposes.length ? <span className="text-ink-2"> · {row.purposes.slice(0, 2).join(", ")}</span> : null}
                </span>
                <span className="shrink-0 num text-ink-2">{formatInt(total)} tok</span>
              </div>
              <div
                className="mt-1 flex h-2 overflow-hidden bg-off"
                role="img"
                aria-label={`${row.runId}: ${formatInt(row.frontier)} frontier tokens, ${formatInt(row.small)} small tokens`}
              >
                {row.frontier > 0 ? (
                  <div data-testid="model-usage-chart-row-frontier" className="bg-live" style={{ width: `${frontierPct}%` }} />
                ) : null}
                {row.small > 0 ? (
                  <div data-testid="model-usage-chart-row-small" className="bg-ink-2" style={{ width: `${smallPct}%` }} />
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {hidden > 0 ? (
        <p data-testid="model-usage-chart-more" className="text-[11px] text-ink-2 num">
          + {hidden} more run{hidden === 1 ? "" : "s"} (showing top {visible.length})
        </p>
      ) : null}
    </div>
  );
}
