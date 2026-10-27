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
  [key: string]: unknown;
};

type Props = {
  modelUsage: ModelUsage | null | undefined;
  title?: string;
  compact?: boolean;
};

function toNumber(v: unknown, fallback = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

export function ModelUsageView({ modelUsage, title, compact }: Props) {
  if (!modelUsage || Object.keys(modelUsage).length === 0) {
    return (
      <div data-testid="model-usage-empty" className="border border-dashed px-4 py-4 text-center text-sm text-ink-2">
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
  const totalCost = modelUsage.total_cost != null ? String(modelUsage.total_cost) : null;

  const hasFrontier = frontierTokens > 0;
  const totalTokens = frontierTokens + smallTokens;

  return (
    <div data-testid="model-usage-view" className="overflow-hidden border bg-band">
      <div className="border-b bg-ground px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">{title ?? "Model usage"}</h3>
      </div>

      {/* Summary row */}
      <div className="grid grid-cols-3 gap-0 divide-x border-b">
        <div className="px-4 py-3 text-center">
          <div data-testid="model-usage-frontier-tokens" className={`text-sm font-semibold ${hasFrontier ? "text-amber-700" : "text-ink-2"}`}>
            {frontierTokens}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-ink-2">Frontier tokens</div>
        </div>
        <div className="px-4 py-3 text-center">
          <div data-testid="model-usage-small-tokens" className="text-sm font-semibold text-ink-2">
            {smallTokens}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-ink-2">Small tokens</div>
        </div>
        <div className="px-4 py-3 text-center">
          <div data-testid="model-usage-calls" className="text-sm font-semibold text-ink-2">
            {totalCalls}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-ink-2">Calls</div>
        </div>
      </div>

      {/* Zero-frontier badge (important for W4/W6: known repairs show 0 frontier) */}
      {frontierTokens === 0 ? (
        <div className="border-b bg-emerald-50 px-4 py-2 text-center">
          <span data-testid="model-usage-zero-frontier" className="inline-flex bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800">
            Zero frontier tokens — deterministic repair
          </span>
        </div>
      ) : null}

      {!compact ? (
        <div className="space-y-3 px-4 py-3">
          {/* Token bar */}
          {totalTokens > 0 ? (
            <div data-testid="model-usage-bar" className="space-y-1">
              <div className="flex h-2 overflow-hidden bg-ground">
                {frontierTokens > 0 ? (
                  <div
                    data-testid="model-usage-bar-frontier"
                    className="bg-amber-400"
                    style={{ width: `${(frontierTokens / totalTokens) * 100}%` }}
                  />
                ) : null}
                {smallTokens > 0 ? (
                  <div
                    data-testid="model-usage-bar-small"
                    className="bg-violet-400"
                    style={{ width: `${(smallTokens / totalTokens) * 100}%` }}
                  />
                ) : null}
              </div>
              <div className="flex justify-between text-[11px] text-ink-2">
                <span>Frontier {frontierTokens} / Small {smallTokens}</span>
                <span>Total {totalTokens} tokens</span>
              </div>
            </div>
          ) : null}

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
            {totalCost != null && totalCost !== "0" ? (
              <div>
                <dt className="text-ink-2">Est. cost</dt>
                <dd data-testid="model-usage-cost" className="font-mono">
                  ${totalCost}
                </dd>
              </div>
            ) : null}
          </dl>

          {/* Raw JSON toggle */}
          <details className="border bg-ground px-3 py-2">
            <summary data-testid="model-usage-raw-toggle" className="cursor-pointer text-xs text-ink-2 hover:text-ink-2">
              Show raw model_usage
            </summary>
            <pre data-testid="model-usage-raw" className="mt-2 max-h-60 overflow-auto bg-band p-3 font-mono text-xs text-ink-2">
              {JSON.stringify(modelUsage, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </div>
  );
}
