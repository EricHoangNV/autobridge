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
      <div data-testid="model-usage-empty" className="rounded-lg border border-dashed px-4 py-4 text-center text-sm text-zinc-500">
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
    <div data-testid="model-usage-view" className="overflow-hidden rounded-lg border bg-white">
      <div className="border-b bg-zinc-50 px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-600">{title ?? "Model usage"}</h3>
      </div>

      {/* Summary row */}
      <div className="grid grid-cols-3 gap-0 divide-x border-b">
        <div className="px-4 py-3 text-center">
          <div data-testid="model-usage-frontier-tokens" className={`text-sm font-semibold ${hasFrontier ? "text-amber-700" : "text-zinc-700"}`}>
            {frontierTokens}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Frontier tokens</div>
        </div>
        <div className="px-4 py-3 text-center">
          <div data-testid="model-usage-small-tokens" className="text-sm font-semibold text-zinc-700">
            {smallTokens}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Small tokens</div>
        </div>
        <div className="px-4 py-3 text-center">
          <div data-testid="model-usage-calls" className="text-sm font-semibold text-zinc-700">
            {totalCalls}
          </div>
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">Calls</div>
        </div>
      </div>

      {/* Zero-frontier badge (important for W4/W6: known repairs show 0 frontier) */}
      {frontierTokens === 0 ? (
        <div className="border-b bg-emerald-50 px-4 py-2 text-center">
          <span data-testid="model-usage-zero-frontier" className="inline-flex rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800">
            Zero frontier tokens — deterministic repair
          </span>
        </div>
      ) : null}

      {!compact ? (
        <div className="space-y-3 px-4 py-3">
          {/* Token bar */}
          {totalTokens > 0 ? (
            <div data-testid="model-usage-bar" className="space-y-1">
              <div className="flex h-2 overflow-hidden rounded-full bg-zinc-100">
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
              <div className="flex justify-between text-[11px] text-zinc-500">
                <span>Frontier {frontierTokens} / Small {smallTokens}</span>
                <span>Total {totalTokens} tokens</span>
              </div>
            </div>
          ) : null}

          {/* Detail rows */}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <div>
              <dt className="text-zinc-500">Frontier calls</dt>
              <dd data-testid="model-usage-frontier-calls" className="font-mono font-medium">
                {toNumber(modelUsage.frontier_calls)}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">Small calls</dt>
              <dd data-testid="model-usage-small-calls" className="font-mono font-medium">
                {toNumber(modelUsage.small_calls)}
              </dd>
            </div>
            {totalInput > 0 || totalOutput > 0 ? (
              <>
                <div>
                  <dt className="text-zinc-500">Input tokens</dt>
                  <dd className="font-mono">{totalInput}</dd>
                </div>
                <div>
                  <dt className="text-zinc-500">Output tokens</dt>
                  <dd className="font-mono">{totalOutput}</dd>
                </div>
              </>
            ) : null}
            {totalCost != null && totalCost !== "0" ? (
              <div>
                <dt className="text-zinc-500">Est. cost</dt>
                <dd data-testid="model-usage-cost" className="font-mono">
                  ${totalCost}
                </dd>
              </div>
            ) : null}
          </dl>

          {/* Raw JSON toggle */}
          <details className="rounded border bg-zinc-50 px-3 py-2">
            <summary data-testid="model-usage-raw-toggle" className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-700">
              Show raw model_usage
            </summary>
            <pre data-testid="model-usage-raw" className="mt-2 max-h-60 overflow-auto rounded bg-white p-3 font-mono text-xs text-zinc-700">
              {JSON.stringify(modelUsage, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </div>
  );
}
