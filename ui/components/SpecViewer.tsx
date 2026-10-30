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

/** Anchor id for a top-level spec section (the viewer's toc scrolls to these). */
export function specSectionAnchor(key: string): string {
  return `spec-sec-${key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

/** Split our own YAML output into per-top-level-key chunks for section anchors. */
export function splitYamlSections(yaml: string): { key: string; text: string }[] {
  const lines = yaml.split("\n");
  const sections: { key: string; text: string }[] = [];
  let current: string[] | null = null;
  let currentKey = "";
  for (const line of lines) {
    const top = line.match(/^([^\s][^:]*):$/);
    if (top && current !== null) {
      sections.push({ key: currentKey, text: current.join("\n") });
      current = null;
    }
    if (top && current === null) {
      currentKey = top[1];
      current = [line];
    } else if (current !== null) {
      current.push(line);
    } else {
      // Leading lines before the first top-level key (should not happen).
      currentKey = "";
      current = [line];
    }
  }
  if (current !== null) sections.push({ key: currentKey, text: current.join("\n") });
  return sections;
}

type Props = {
  spec: Record<string, unknown> | null | undefined;
  title?: string;
  /** Full-value tooltip for the (possibly truncated) title, e.g. release id + signature. */
  titleTooltip?: string;
};

export function SpecViewer({ spec, title, titleTooltip }: Props) {
  const [copied, setCopied] = useState(false);
  const yaml = useMemo(() => (spec ? specToYaml(spec as Record<string, unknown>) : ""), [spec]);
  const sections = useMemo(() => splitYamlSections(yaml), [yaml]);

  if (!spec || Object.keys(spec).length === 0) {
    return (
      <div data-testid="spec-viewer-empty" className="border border-rule px-4 py-6 text-sm text-ink-2">
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
    <div data-testid="spec-viewer" className="spec-viewer-box">
      <div className="mb-2 flex items-center justify-between gap-4">
        <span
          className="text-sm text-ink-2"
          tabIndex={titleTooltip ? 0 : undefined}
          title={titleTooltip}
        >
          {title ?? "Spec (YAML)"}
        </span>
        <button
          data-testid="spec-copy-btn"
          onClick={handleCopy}
          className="link"
        >
          {copied ? "Copied!" : "Copy YAML"}
        </button>
      </div>
      <pre
        data-testid="spec-yaml"
        className="cyaml spec-yaml-scroll"
      >
        {sections.map((s, i) => (
          <span key={s.key || "top"} id={s.key ? specSectionAnchor(s.key) : undefined}>
            {s.text}
            {i < sections.length - 1 ? "\n" : ""}
          </span>
        ))}
      </pre>
      <details className="mt-2">
        <summary data-testid="spec-json-toggle" className="cursor-pointer text-sm text-ink-2 hover:text-ink">
          Show raw JSON
        </summary>
        <pre
          data-testid="spec-json"
          className="cyaml mt-2"
        >
          {JSON.stringify(spec, null, 2)}
        </pre>
      </details>
    </div>
  );
}
