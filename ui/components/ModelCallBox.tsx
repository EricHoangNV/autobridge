"use client";

export type LedgerCall = {
  tier: string;
  purpose: string;
  model?: string;
  mode?: string;
  captured_at?: string;
  outcome?: string | null;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens?: number;
  cost: string;
};

export type LedgerAttempt = {
  rung: number;
  action: string;
  reason: string;
  calls: LedgerCall[];
};

function formatInt(n: number): string {
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "0";
}

function callTokens(c: LedgerCall): number {
  return (c.input_tokens || 0) + (c.output_tokens || 0);
}

/**
 * Model-call box (REQ-032 §3): every call with rung, model, live or replay
 * with capture date, tokens in/out/reasoning, cost and outcome — and an
 * explicit "0 model calls" when the repair used none. All rows come from
 * `GET /repairs/{id}/ledger`. While a repair is still climbing (`pending`),
 * an empty box means no call has landed *yet*, not that none ran.
 */
export function ModelCallBox({ attempts, pending = false }: { attempts: LedgerAttempt[]; pending?: boolean }) {
  const rows: Array<{ rung: number; call: LedgerCall }> = [];
  for (const a of attempts ?? []) {
    for (const call of a.calls ?? []) rows.push({ rung: a.rung, call });
  }
  const totalCost = rows.reduce((acc, { call }) => {
    const n = Number(call.cost);
    return acc + (Number.isFinite(n) ? n : 0);
  }, 0);
  const totalReasoning = rows.reduce((acc, { call }) => acc + (call.reasoning_tokens || 0), 0);
  if (!rows.length) {
    return (
      <p data-testid="model-call-box-empty" className="font-mono text-sm">
        {pending ? "climbing — no calls landed yet" : "0 model calls"}
      </p>
    );
  }
  return (
    <div data-testid="model-call-box">
      <table className="w-full text-left font-mono text-xs">
        <thead className="text-ink-2">
          <tr>
            <th className="py-1 pr-3 font-medium">Rung</th>
            <th className="py-1 pr-3 font-medium">Model</th>
            <th className="py-1 pr-3 font-medium">Live / replay</th>
            <th className="py-1 pr-3 font-medium">Tokens in/out/reasoning</th>
            <th className="py-1 pr-3 font-medium">Cost</th>
            <th className="py-1 font-medium">Outcome</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {rows.map(({ rung, call }, i) => (
            <tr key={i} data-testid={`model-call-row-${i}`}>
              <td className="py-1 pr-3">L{rung}</td>
              <td className="py-1 pr-3">
                {call.model || call.tier || "—"}
                {call.purpose ? <span className="text-ink-2"> · {call.purpose}</span> : null}
              </td>
              <td className="py-1 pr-3">
                {call.mode === "replay" ? `replayed${call.captured_at ? ` ${call.captured_at}` : ""}` : "live"}
              </td>
              <td className="num py-1 pr-3">
                {formatInt(call.input_tokens)}/{formatInt(call.output_tokens)}/{formatInt(call.reasoning_tokens ?? 0)}
              </td>
              <td className="num py-1 pr-3">${Number(call.cost).toFixed(4)}</td>
              <td className="py-1">{call.outcome ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p data-testid="model-call-box-total" className="num mt-1 font-mono text-xs text-ink-2">
        {rows.length} call{rows.length === 1 ? "" : "s"} ·{" "}
        {formatInt(rows.reduce((a, { call }) => a + callTokens(call), 0))} tokens
        {totalReasoning > 0 ? <span> ({formatInt(totalReasoning)} reasoning)</span> : null} · ${totalCost.toFixed(4)}
      </p>
    </div>
  );
}
