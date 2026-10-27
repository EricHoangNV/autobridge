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
  if (op === "add") return "bg-emerald-50 border-emerald-200 text-emerald-900";
  if (op === "remove") return "bg-red-50 border-red-200 text-red-900";
  if (op === "replace") return "bg-amber-50 border-amber-200 text-amber-900";
  if (op === "move" || op === "copy") return "bg-blue-50 border-blue-200 text-blue-900";
  return "bg-zinc-50 border-zinc-200 text-zinc-800";
}

function opBadge(op: string): string {
  if (op === "add") return "bg-emerald-100 text-emerald-800";
  if (op === "remove") return "bg-red-100 text-red-800";
  if (op === "replace") return "bg-amber-100 text-amber-800";
  return "bg-zinc-100 text-zinc-700";
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
      <div data-testid="patch-diff-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
        No patch — repair produced no JSON Patch (backoff-only or escalated without patch).
      </div>
    );
  }

  // Group ops by path for readability
  return (
    <div data-testid="patch-diff" className="overflow-hidden rounded-lg border bg-white">
      <div className="flex items-center justify-between border-b bg-zinc-50 px-4 py-2">
        <span className="text-xs font-medium text-zinc-600">
          {title ?? `JSON Patch — ${ops.length} operation${ops.length === 1 ? "" : "s"}`}
        </span>
        <button
          data-testid="patch-copy-btn"
          onClick={handleCopy}
          className="rounded border bg-white px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-50"
        >
          {copied ? "Copied!" : "Copy patch"}
        </button>
      </div>

      {/* Side-by-side: old vs new — for JSON Patch we show ops list + raw JSON side by side */}
      <div className="grid grid-cols-1 gap-0 md:grid-cols-2 md:divide-x">
        {/* Left: structured ops */}
        <div data-testid="patch-diff-old" className="min-w-0">
          <div className="border-b bg-zinc-50 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            Operations (old → new)
          </div>
          <ul className="divide-y">
            {ops.map((op, idx) => (
              <li
                key={idx}
                data-testid={`patch-op-${idx}`}
                className={`border-l-2 px-3 py-2.5 ${opColor(op.op)}`}
              >
                <div className="flex items-center gap-2">
                  <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${opBadge(op.op)}`}>{op.op}</span>
                  <span data-testid={`patch-op-path-${idx}`} className="font-mono text-xs font-medium">
                    {op.path}
                  </span>
                </div>
                {op.from ? (
                  <div className="mt-1 font-mono text-xs text-zinc-500">from: {op.from}</div>
                ) : null}
                {op.value !== undefined ? (
                  <pre
                    data-testid={`patch-op-value-${idx}`}
                    className="mt-1 max-h-40 overflow-auto rounded bg-white/70 p-2 font-mono text-xs"
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
          <div className="border-b bg-zinc-50 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            Raw JSON Patch
          </div>
          <pre
            data-testid="patch-diff-raw"
            className="max-h-[480px] overflow-auto p-3 font-mono text-xs leading-relaxed text-zinc-800"
          >
            {JSON.stringify(ops, null, 2)}
          </pre>
        </div>
      </div>

      {/* Full patch JSON collapsed */}
      <details className="border-t bg-zinc-50 px-4 py-2">
        <summary data-testid="patch-json-toggle" className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-700">
          Show patch as single line
        </summary>
        <pre data-testid="patch-json-compact" className="mt-2 overflow-auto rounded bg-white p-3 font-mono text-xs text-zinc-700">
          {JSON.stringify(ops)}
        </pre>
      </details>
    </div>
  );
}
