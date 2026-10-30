"use client";

type ModelUsage = {
  frontier_tokens?: number;
  small_tokens?: number;
  calls?: number;
  frontier_calls?: number;
  small_calls?: number;
  frontier_tokens_total?: number;
  small_tokens_total?: number;
  total_calls?: number;
  total_input_tokens?: number;
  total_output_tokens?: number;
  total_cost?: string | number;
  frontier_tokens_legacy?: number;
  wall_seconds?: number;
  [key: string]: unknown;
};

type Props = {
  modelUsage: ModelUsage | null | undefined;
  title?: string;
  compact?: boolean;
  /**
   * Frontier budget the counter is measured against (REQ-048 item 6): the
   * budget that actually applies to the repair (the L3 cap, one source of
   * truth), passed by the repair page from the row/ledger. Null/undefined
   * hides the budget clause instead of inventing a default.
   */
  budget?: number | null;
  /** Live display value for the big counter (the L3 tick); defaults to the final total. */
  liveFrontierTokens?: number | null;
  /** True while the repair is still climbing: the counter is "so far" and the zero badge stays hidden. */
  pending?: boolean;
};

function toNumber(v: unknown, fallback = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

function formatInt(n: number): string {
  return n.toLocaleString("en-US");
}

/** Omit cost keys from the raw dump (REQ-055 item 5: no $ in the UI). */
function stripCostKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(stripCostKeys);
  if (v !== null && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (k.toLowerCase().includes("cost")) continue;
      out[k] = stripCostKeys(val);
    }
    return out;
  }
  return v;
}

export function ModelUsageView({ modelUsage, title, compact, budget = null, liveFrontierTokens = null, pending = false }: Props) {
  if (!modelUsage || Object.keys(modelUsage).length === 0) {
    return (
      <div data-testid="model-usage-empty" className="border border-dashed border-rule px-4 py-4 text-center text-sm text-ink-2">
        No model calls recorded for this repair.
      </div>
    );
  }

  const frontierTokens =
    toNumber(modelUsage.frontier_tokens) ||
    toNumber(modelUsage.frontier_tokens_total) ||
    toNumber(modelUsage.frontier_tokens_legacy);

  const smallTokens =
    toNumber(modelUsage.small_tokens) ||
    toNumber(modelUsage.small_tokens_total);

  const totalCalls =
    toNumber(modelUsage.calls) ||
    toNumber(modelUsage.total_calls) ||
    toNumber(modelUsage.frontier_calls) + toNumber(modelUsage.small_calls);

  const totalInput = toNumber(modelUsage.total_input_tokens);
  const totalOutput = toNumber(modelUsage.total_output_tokens);
  const wallSeconds = toNumber((modelUsage as Record<string, unknown>)["wall_seconds"]);
  // REQ-055 item 5: no dollar amounts in the UI — calls and tokens only.
  // The raw dump below strips cost keys for the same reason; the API and
  // ledger keep their cost fields unchanged.

  const totalTokens = frontierTokens + smallTokens;
  // The big counter is the L3 tick: it holds at 0 through L0–L2 and only
  // counts up once the frontier model has run (REQ-002 slice 3).
  const shownFrontier =
    typeof liveFrontierTokens === "number" && Number.isFinite(liveFrontierTokens) && liveFrontierTokens >= 0
      ? Math.round(liveFrontierTokens)
      : frontierTokens;

  return (
    <div data-testid="model-usage-view">
      <div data-testid="model-usage-frontier-tokens" className="repair-tok num" aria-label={`${formatInt(shownFrontier)} frontier tokens`}>
        {formatInt(shownFrontier)}
      </div>
      <p className="repair-tok-l num">
        {pending ? "frontier tokens so far" : (title ?? "frontier tokens")}
        {typeof budget === "number" && Number.isFinite(budget) && budget > 0 ? ` of a ${formatInt(budget)} budget` : ""}
      </p>

      {/* Zero-frontier badge (important for W4/W6: known repairs show 0 frontier).
          Hidden while climbing: 0 "so far" is not the verdict yet. */}
      {frontierTokens === 0 && !pending ? (
        <div className="mt-2">
          <span data-testid="model-usage-zero-frontier" className="inline-flex border border-rule px-2.5 py-0.5 text-xs font-medium text-ink">
            Zero frontier tokens — deterministic repair
          </span>
        </div>
      ) : null}

      <p data-testid="model-usage-bar" className="repair-split num">
        <span>
          Small model <b data-testid="model-usage-small-tokens">{formatInt(smallTokens)}</b>
        </span>
        <span>
          Calls <b data-testid="model-usage-calls">{formatInt(totalCalls)}</b>
        </span>
        {wallSeconds > 0 ? (
          <span>
            Wall <b>{wallSeconds} s</b>
          </span>
        ) : null}
      </p>

      {/* Token bar */}
      {totalTokens > 0 ? (
        <div className="mt-2 space-y-1">
          <div className="flex h-1.5 overflow-hidden bg-off" aria-hidden="true">
            {frontierTokens > 0 ? (
              <div
                data-testid="model-usage-bar-frontier"
                className="bg-live"
                style={{ width: `${(frontierTokens / totalTokens) * 100}%` }}
              />
            ) : null}
            {smallTokens > 0 ? (
              <div
                data-testid="model-usage-bar-small"
                className="bg-ink-2"
                style={{ width: `${(smallTokens / totalTokens) * 100}%` }}
              />
            ) : null}
          </div>
          <div className="flex justify-between text-[11px] text-ink-2">
            <span>
              Frontier {formatInt(frontierTokens)} / Small {formatInt(smallTokens)}
            </span>
            <span>Total {formatInt(totalTokens)} tokens</span>
          </div>
        </div>
      ) : null}

      {!compact ? (
        <div className="mt-3 space-y-3">
          {/* Detail rows */}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <div>
              <dt className="text-ink-2">Frontier calls</dt>
              <dd data-testid="model-usage-frontier-calls" className="font-mono font-medium">
                {toNumber(modelUsage.frontier_calls)}
              </dd>
            </div>
            <div>
              <dt className="text-ink-2">Small calls</dt>
              <dd data-testid="model-usage-small-calls" className="font-mono font-medium">
                {toNumber(modelUsage.small_calls)}
              </dd>
            </div>
            {totalInput > 0 || totalOutput > 0 ? (
              <>
                <div>
                  <dt className="text-ink-2">Input tokens</dt>
                  <dd className="font-mono">{totalInput}</dd>
                </div>
                <div>
                  <dt className="text-ink-2">Output tokens</dt>
                  <dd className="font-mono">{totalOutput}</dd>
                </div>
              </>
            ) : null}
          </dl>

          {/* Raw JSON toggle (cost keys stripped — see above) */}
          <details className="border border-rule px-3 py-2">
            <summary data-testid="model-usage-raw-toggle" className="cursor-pointer text-xs text-ink-2 hover:text-ink">
              Show raw model_usage
            </summary>
            <pre data-testid="model-usage-raw" className="mt-2 max-h-60 overflow-auto bg-ground p-3 font-mono text-xs text-ink-2">
              {JSON.stringify(stripCostKeys(modelUsage), null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </div>
  );
}
