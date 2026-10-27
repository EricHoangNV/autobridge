"use client";

import { useState, useMemo } from "react";

function specToYaml(spec: Record<string, unknown>): string {
  // Minimal YAML serializer — avoids adding js-yaml dep for a read-only viewer.
  // Handles nested objects, arrays of scalars / objects, strings, numbers, booleans.
  const lines: string[] = [];
  function writeValue(value: unknown, indent: number): void {
    if (value === null || value === undefined) {
      lines[lines.length - 1] += " null";
      return;
    }
    if (typeof value === "string") {
      // Quote strings that need it (contains special chars or is empty)
      const needsQuote = value === "" || /[:#\[\]{}&*?|\->!%@`]/.test(value) || /^\s|\s$/.test(value);
      lines[lines.length - 1] += needsQuote ? ` "${value.replace(/"/g, '\\"')}"` : ` ${value}`;
      return;
    }
    if (typeof value === "number" || typeof value === "boolean") {
      lines[lines.length - 1] += ` ${String(value)}`;
      return;
    }
    if (Array.isArray(value)) {
      if (value.length === 0) {
        lines[lines.length - 1] += " []";
        return;
      }
      for (const item of value) {
        if (item !== null && typeof item === "object" && !Array.isArray(item)) {
          const entries = Object.entries(item as Record<string, unknown>);
          if (entries.length === 0) {
            lines.push(`${"  ".repeat(indent)}- {}`);
            continue;
          }
          // First key inline after dash
          const [firstKey, firstVal] = entries[0];
          lines.push(`${"  ".repeat(indent)}- ${firstKey}:`);
          writeValue(firstVal, indent + 1);
          for (const [k, v] of entries.slice(1)) {
            lines.push(`${"  ".repeat(indent + 1)}${k}:`);
            writeValue(v, indent + 2);
          }
        } else {
          lines.push(`${"  ".repeat(indent)}-`);
          writeValue(item, indent + 1);
          // writeValue appends to last line; need to fix single-scalar items
          // For scalar items, the line is already "- value" — done
        }
      }
      return;
    }
    if (typeof value === "object") {
      const entries = Object.entries(value as Record<string, unknown>);
      if (entries.length === 0) {
        lines[lines.length - 1] += " {}";
        return;
      }
      for (const [k, v] of entries) {
        lines.push(`${"  ".repeat(indent)}${k}:`);
        writeValue(v, indent + 1);
      }
    }
  }
  // Top-level: each key
  for (const [k, v] of Object.entries(spec)) {
    lines.push(`${k}:`);
    writeValue(v, 1);
  }
  return lines.join("\n");
}

export function specToYamlString(spec: Record<string, unknown>): string {
  return specToYaml(spec);
}

type Props = {
  spec: Record<string, unknown> | null | undefined;
  title?: string;
};

export function SpecViewer({ spec, title }: Props) {
  const [copied, setCopied] = useState(false);
  const yaml = useMemo(() => (spec ? specToYaml(spec as Record<string, unknown>) : ""), [spec]);

  if (!spec || Object.keys(spec).length === 0) {
    return (
      <div data-testid="spec-viewer-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
        No spec available for this release.
      </div>
    );
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(yaml);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard not available (e.g. test env)
    }
  }

  return (
    <div data-testid="spec-viewer" className="overflow-hidden rounded-lg border bg-white">
      <div className="flex items-center justify-between border-b bg-zinc-50 px-4 py-2">
        <span className="text-xs font-medium text-zinc-600">{title ?? "Spec (YAML)"}</span>
        <button
          data-testid="spec-copy-btn"
          onClick={handleCopy}
          className="rounded border bg-white px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-50"
        >
          {copied ? "Copied!" : "Copy YAML"}
        </button>
      </div>
      <pre
        data-testid="spec-yaml"
        className="max-h-[480px] overflow-auto px-4 py-3 font-mono text-xs leading-relaxed text-zinc-800"
      >
        {yaml}
      </pre>
      <details className="border-t bg-zinc-50 px-4 py-2">
        <summary data-testid="spec-json-toggle" className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-700">
          Show raw JSON
        </summary>
        <pre
          data-testid="spec-json"
          className="mt-2 max-h-[360px] overflow-auto rounded bg-white p-3 font-mono text-xs text-zinc-700"
        >
          {JSON.stringify(spec, null, 2)}
        </pre>
      </details>
    </div>
  );
}
