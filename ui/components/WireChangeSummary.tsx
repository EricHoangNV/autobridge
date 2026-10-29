"use client";

type Delta = {
  pagination_changed?: boolean;
  keys_changed?: boolean;
  types_changed?: boolean;
  changed_entities?: string[];
  pagination_details?: Record<string, unknown>;
  keys_details?: Record<string, unknown>;
  types_details?: Record<string, unknown>;
};

function short(v: unknown, max = 48): string {
  let s: string;
  try {
    s = typeof v === "string" ? v : JSON.stringify(v);
  } catch {
    s = String(v);
  }
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function strategyOf(shape: unknown): string {
  if (shape !== null && typeof shape === "object" && !Array.isArray(shape)) {
    const s = (shape as Record<string, unknown>)["strategy"];
    if (typeof s === "string" && s) return s;
  }
  return short(shape);
}

/**
 * Plain-language before/after lines from the persisted fingerprint delta
 * (REQ-032 §3 "what changed on the wire"). Best-effort and honest: every
 * line quotes observed previous/current values; when the delta carries no
 * change it says so instead of inventing one.
 */
export function summarizeWireChange(driftClass: string | null | undefined, delta: Delta | null | undefined): string[] {
  const lines: string[] = [];
  if (!delta || typeof delta !== "object") {
    return ["No wire-shape change was recorded for this drift."];
  }
  const pag = delta.pagination_details ?? {};
  for (const [op, detail] of Object.entries(pag)) {
    if (detail !== null && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as Record<string, unknown>;
      if ("previous" in d || "current" in d) {
        lines.push(`pagination on ${op}: ${strategyOf(d["previous"])} → ${strategyOf(d["current"])}`);
        continue;
      }
    }
    lines.push(`pagination on ${op} changed: ${short(detail)}`);
  }
  const keys = delta.keys_details ?? {};
  for (const [entity, detail] of Object.entries(keys)) {
    if (detail !== null && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as Record<string, unknown>;
      const prev = d["previous"];
      const cur = d["current"];
      if (Array.isArray(prev) && Array.isArray(cur)) {
        const removed = prev.filter((k) => !cur.includes(k)).map(String);
        const added = cur.filter((k) => !prev.includes(k)).map(String);
        if (removed.length === 1 && added.length === 1 && prev.length === cur.length) {
          lines.push(`field \`${removed[0]}\` renamed \`${added[0]}\` on ${entity}`);
        } else {
          const bits: string[] = [];
          if (removed.length) bits.push(`removed ${removed.map((k) => `\`${k}\``).join(", ")}`);
          if (added.length) bits.push(`added ${added.map((k) => `\`${k}\``).join(", ")}`);
          lines.push(`fields on ${entity}: ${bits.join("; ") || "reordered"}`);
        }
        continue;
      }
      if ("previous" in d || "current" in d) {
        lines.push(`fields on ${entity}: ${short(prev)} → ${short(cur)}`);
        continue;
      }
    }
    lines.push(`fields on ${entity} changed: ${short(detail)}`);
  }
  const types = delta.types_details ?? {};
  for (const [entity, detail] of Object.entries(types)) {
    if (detail !== null && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as Record<string, unknown>;
      const prev = d["previous"];
      const cur = d["current"];
      if (prev !== null && typeof prev === "object" && !Array.isArray(prev) && cur !== null && typeof cur === "object" && !Array.isArray(cur)) {
        const p = prev as Record<string, unknown>;
        const c = cur as Record<string, unknown>;
        const fields = [...new Set([...Object.keys(p), ...Object.keys(c)])].filter((f) => short(p[f]) !== short(c[f]));
        for (const f of fields.slice(0, 5)) {
          lines.push(`field \`${f}\` on ${entity}: ${short(p[f])} → ${short(c[f])}`);
        }
        if (fields.length > 5) lines.push(`…and ${fields.length - 5} more field change${fields.length - 5 === 1 ? "" : "s"} on ${entity}`);
        continue;
      }
    }
    lines.push(`values on ${entity} changed: ${short(detail)}`);
  }
  if (!lines.length) {
    if (driftClass) {
      const words = driftClass.toLowerCase().replace(/_/g, " ");
      lines.push(`${words.charAt(0).toUpperCase() + words.slice(1)}: no wire-shape change was recorded.`);
    } else {
      lines.push("No wire-shape change was recorded for this drift.");
    }
  }
  return lines;
}

export function WireChangeSummary({
  driftClass,
  delta,
}: {
  driftClass: string | null | undefined;
  delta: Delta | null | undefined;
}) {
  const lines = summarizeWireChange(driftClass, delta);
  return (
    <ul data-testid="wire-change-summary" className="list-none space-y-1 p-0 font-mono text-xs">
      {lines.map((line, i) => (
        <li key={i} data-testid={`wire-change-line-${i}`}>
          {line}
        </li>
      ))}
    </ul>
  );
}
