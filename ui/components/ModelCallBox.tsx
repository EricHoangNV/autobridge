"use client";

import { Fragment } from "react";

export type LedgerCall = {
  tier: string;
  purpose: string;
  model?: string;
  mode?: string;
  captured_at?: string;
  outcome?: string | null;
  prompt_id?: string;
  prompt_version?: string;
  prompt_hash?: string;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens?: number;
  cost: string;
};

export type LedgerProposal = {
  patch: Array<{ op: string; path: string; value?: unknown; from?: string }>;
  passed: boolean;
  error: string;
};

export type LedgerAttempt = {
  rung: number;
  action: string;
  reason: string;
  calls: LedgerCall[];
  proposals?: LedgerProposal[];
};

function formatInt(n: number): string {
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "0";
}

function formatOpValue(v: unknown): string {
  if (v === undefined) return "unset";
  try {
    const s = JSON.stringify(v);
    return s === undefined ? "unset" : s;
  } catch {
    return String(v);
  }
}

function callTokens(c: LedgerCall): number {
  return (c.input_tokens || 0) + (c.output_tokens || 0);
}

/**
 * Model-call box (REQ-032 §3): every call with rung, model, live or replay
 * with capture date, tokens in/out/reasoning and outcome — and an explicit
 * "0 model calls" when the repair used none. All rows come from
 * `GET /repairs/{id}/ledger`. While a repair is still climbing (`pending`),
 * an empty box means no call has landed *yet*, not that none ran.
 *
 * REQ-055 item 5: no dollar amounts — calls and tokens only (the API and
 * ledger keep their cost fields; the UI does not render them).
 * REQ-055 item 4b: each L3 call shows its proposal (op/path/value) and the
 * dry-run verdict — "passed dry-run" or "rejected by dry-run: <reason>".
 */
export function ModelCallBox({ attempts, pending = false }: { attempts: LedgerAttempt[]; pending?: boolean }) {
  const rows: Array<{ rung: number; call: LedgerCall; proposal?: LedgerProposal }> = [];
  for (const a of attempts ?? []) {
    (a.calls ?? []).forEach((call, j) => {
      rows.push({ rung: a.rung, call, proposal: a.proposals?.[j] });
    });
  }
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
            <th className="py-1 font-medium">Outcome</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {rows.map(({ rung, call, proposal }, i) => (
            <Fragment key={i}>
              <tr data-testid={`model-call-row-${i}`}>
                <td className="py-1 pr-3">L{rung}</td>
                <td className="py-1 pr-3">
                  {call.model || call.tier || "—"}
                  {call.purpose ? <span className="text-ink-2"> · {call.purpose}</span> : null}
                  {call.prompt_id ? (
                    <span className="text-ink-2" title={call.prompt_hash || undefined}>
                      {" "}· prompt {call.prompt_id}
                      {call.prompt_version ? ` v${call.prompt_version}` : ""}
                    </span>
                  ) : null}
                </td>
                <td className="py-1 pr-3">
                  {call.mode === "replay" ? `replayed${call.captured_at ? ` ${call.captured_at}` : ""}` : "live"}
                </td>
                <td className="num py-1 pr-3">
                  {formatInt(call.input_tokens)}/{formatInt(call.output_tokens)}/{formatInt(call.reasoning_tokens ?? 0)}
                </td>
                <td className="py-1">{call.outcome ?? "—"}</td>
              </tr>
              {proposal ? (
                <tr data-testid={`model-call-proposal-${i}`}>
                  <td className="py-1 pr-3 text-ink-2">↳</td>
                  <td colSpan={4} className="py-1">
                    {proposal.patch.length ? (
                      <span>
                        {proposal.patch.map((op, k) => (
                          <span key={k} data-testid={`model-call-proposal-op-${i}-${k}`} className="mr-2">
                            {op.op} {op.path}
                            {op.value !== undefined ? ` → ${formatOpValue(op.value)}` : ""}
                            {k < proposal.patch.length - 1 ? ";" : ""}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="text-ink-2">no proposal</span>
                    )}{" "}
                    <span data-testid={`model-call-verdict-${i}`} className="text-ink-2">
                      {proposal.passed
                        ? "— passed dry-run"
                        : proposal.patch.length && proposal.error
                          ? `— rejected by dry-run: ${proposal.error}`
                          : "— no proposal"}
                    </span>
                  </td>
                </tr>
              ) : null}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p data-testid="model-call-box-total" className="num mt-1 font-mono text-xs text-ink-2">
        {rows.length} call{rows.length === 1 ? "" : "s"} ·{" "}
        {formatInt(rows.reduce((a, { call }) => a + callTokens(call), 0))} tokens
        {totalReasoning > 0 ? <span> ({formatInt(totalReasoning)} reasoning)</span> : null}
      </p>
    </div>
  );
}
