"use client";

import { useState } from "react";

type PatchOp = {
  op: string;
  path: string;
  value?: unknown;
  from?: string;
};

type Props = {
  candidatePatch: PatchOp[] | null | undefined;
  oldSpec?: Record<string, unknown> | null;
  newSpec?: Record<string, unknown> | null;
  title?: string;
};

function formatPatchValue(value: unknown): string {
  if (value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function opColor(op: string): string {
  if (op === "add") return "bg-band border-live text-ink";
  if (op === "remove") return "bg-band border-stop text-stop";
  if (op === "replace") return "bg-band border-rule text-ink";
  if (op === "move" || op === "copy") return "bg-band border-rule text-ink-2";
  return "bg-band border-rule text-ink";
}

function opBadge(op: string): string {
  if (op === "add") return "bg-live text-live-ink";
  if (op === "remove") return "bg-band text-stop border border-stop";
  if (op === "replace") return "bg-ink text-ground";
  return "bg-ground text-ink-2";
}

export function PatchDiff({ candidatePatch, oldSpec: _oldSpec, newSpec: _newSpec, title }: Props) {
  const [copied, setCopied] = useState(false);
  const ops = candidatePatch ?? [];

  const hasPatch = ops.length > 0;

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(ops, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard not available
    }
  }

  if (!hasPatch) {
    return (
      <div data-testid="patch-diff-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
        No patch — repair produced no JSON Patch (backoff-only or escalated without patch).
      </div>
    );
  }

  // Group ops by path for readability
  return (
    <div data-testid="patch-diff" className="overflow-hidden border bg-band">
      <div className="flex items-center justify-between border-b bg-ground px-4 py-2">
        <span className="text-xs font-medium text-ink-2">
          {title ?? `JSON Patch — ${ops.length} operation${ops.length === 1 ? "" : "s"}`}
        </span>
        <button
          data-testid="patch-copy-btn"
          onClick={handleCopy}
          className="border bg-band px-2 py-1 text-xs text-ink-2 hover:bg-ground"
        >
          {copied ? "Copied!" : "Copy patch"}
        </button>
      </div>

      {/* Side-by-side: old vs new — for JSON Patch we show ops list + raw JSON side by side */}
      <div className="grid grid-cols-1 gap-0 md:grid-cols-2 md:divide-x md:divide-rule">
        {/* Left: structured ops */}
        <div data-testid="patch-diff-old" className="min-w-0">
          <div className="border-b bg-ground px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-2">
            Operations (old → new)
          </div>
          <ul className="divide-y divide-rule">
            {ops.map((op, idx) => (
              <li
                key={idx}
                data-testid={`patch-op-${idx}`}
                className={`border-l-2 px-3 py-2.5 ${opColor(op.op)}`}
              >
                <div className="flex items-center gap-2">
                  <span className={`inline-flex px-1.5 py-0.5 text-[11px] font-medium ${opBadge(op.op)}`}>{op.op}</span>
                  <span data-testid={`patch-op-path-${idx}`} className="font-mono text-xs font-medium">
                    {op.path}
                  </span>
                </div>
                {op.from ? (
                  <div className="mt-1 font-mono text-xs text-ink-2">from: {op.from}</div>
                ) : null}
                {op.value !== undefined ? (
                  <pre
                    data-testid={`patch-op-value-${idx}`}
                    className="mt-1 max-h-40 overflow-auto bg-band/70 p-2 font-mono text-xs"
                  >
                    {formatPatchValue(op.value)}
                  </pre>
                ) : null}
              </li>
            ))}
          </ul>
        </div>

        {/* Right: raw JSON */}
        <div data-testid="patch-diff-new" className="min-w-0">
          <div className="border-b bg-ground px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-2">
            Raw JSON Patch
          </div>
          <pre
            data-testid="patch-diff-raw"
            className="max-h-[480px] overflow-auto p-3 font-mono text-xs leading-relaxed text-ink"
          >
            {JSON.stringify(ops, null, 2)}
          </pre>
        </div>
      </div>

      {/* Full patch JSON collapsed */}
      <details className="border-t bg-ground px-4 py-2">
        <summary data-testid="patch-json-toggle" className="cursor-pointer text-xs text-ink-2 hover:text-ink-2">
          Show patch as single line
        </summary>
        <pre data-testid="patch-json-compact" className="mt-2 overflow-auto bg-band p-3 font-mono text-xs text-ink-2">
          {JSON.stringify(ops)}
        </pre>
      </details>
    </div>
  );
}
