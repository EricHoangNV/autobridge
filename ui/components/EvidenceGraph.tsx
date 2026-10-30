"use client";

type DriftEvent = {
  id: string;
  connector_id: string;
  drift_class: string;
  evidence_ids: string[];
};

type EvidenceRecord = {
  id: string;
  kind: string;
  parent_ids: string[];
  run_id: string;
};

type Props = {
  driftEvents: DriftEvent[];
  evidenceRecords?: EvidenceRecord[] | null;
  loading?: boolean;
  /** Cap on rendered nodes (highest fan-in first is not needed; insertion order). Default 40. */
  maxNodes?: number;
};

type GraphNode = {
  id: string;
  label: string;
  sub: string;
  kind: "drift" | "evidence" | "external";
};

type GraphEdge = {
  from: string;
  to: string;
};

const ROW_H = 46;
const NODE_W_MIN = 180;
const NODE_H = 30;
const PAD = 16;
/** Monospace advance, px per char, for the two label sizes below. */
const LABEL_PX = 6.6;
const SUB_PX = 6.0;
const COL_GAP = 40;

/** Deterministic layered layout: depth = longest path from a root (BFS). */
function layout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  colW: number,
): Map<string, { x: number; y: number; depth: number }> {
  const incoming = new Map<string, string[]>();
  for (const n of nodes) incoming.set(n.id, []);
  for (const e of edges) {
    if (incoming.has(e.to)) incoming.get(e.to)!.push(e.from);
  }
  const depth = new Map<string, number>();
  const visit = (id: string, stack: Set<string>): number => {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;
    if (stack.has(id)) return 0; // cycle guard: break at current path
    stack.add(id);
    let d = 0;
    for (const parent of incoming.get(id) ?? []) {
      d = Math.max(d, visit(parent, stack) + 1);
    }
    stack.delete(id);
    depth.set(id, d);
    return d;
  };
  for (const n of nodes) visit(n.id, new Set());

  const byDepth = new Map<number, string[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d)!.push(n.id);
  }
  const pos = new Map<string, { x: number; y: number; depth: number }>();
  for (const [d, ids] of [...byDepth.entries()].sort((a, b) => a[0] - b[0])) {
    ids.forEach((id, i) => {
      pos.set(id, { x: PAD + d * colW, y: PAD + i * ROW_H, depth: d });
    });
  }
  return pos;
}

function shortId(id: string): string {
  return id.length > 12 ? id.slice(0, 8) : id;
}

/**
 * P2.S6.1 — evidence graph view (read-only, no new API surface).
 *
 * Renders drift events and their evidence records as nodes, with edges for
 * drift→evidence links (`evidence_ids`) and evidence→parent links
 * (`parent_ids`). Parents not present in the record set appear as stub
 * "external" nodes so dangling references are visible instead of dropped.
 * Pure SVG + a screen-reader list fallback; no animation, no new dependency.
 */
export function EvidenceGraph({ driftEvents, evidenceRecords, loading, maxNodes = 40 }: Props) {
  if (loading) {
    return (
      <div data-testid="evidence-graph-loading" className="border border-dashed px-4 py-4 text-center text-sm text-ink-2">
        Loading evidence graph…
      </div>
    );
  }

  if (!driftEvents.length) {
    return (
      <div data-testid="evidence-graph-empty" className="border border-dashed border-rule px-4 py-4 text-center text-sm text-ink-2">
        No drift events to graph.
      </div>
    );
  }

  const records = evidenceRecords ?? [];
  const recordById = new Map(records.map((r) => [r.id, r]));

  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  const addNode = (n: GraphNode) => {
    if (!seen.has(n.id)) {
      seen.add(n.id);
      nodes.push(n);
    }
  };

  for (const d of driftEvents) {
    addNode({ id: d.id, label: "drift", sub: `${d.drift_class} · ${shortId(d.id)}`, kind: "drift" });
    for (const eid of d.evidence_ids) {
      const rec = recordById.get(eid);
      if (rec) {
        addNode({ id: rec.id, label: rec.kind, sub: shortId(rec.id), kind: "evidence" });
      } else {
        addNode({ id: eid, label: "missing", sub: shortId(eid), kind: "external" });
      }
      edges.push({ from: d.id, to: eid });
    }
  }
  for (const rec of records) {
    addNode({ id: rec.id, label: rec.kind, sub: shortId(rec.id), kind: "evidence" });
    for (const pid of rec.parent_ids) {
      if (!recordById.has(pid) && !seen.has(pid)) {
        addNode({ id: pid, label: "external", sub: shortId(pid), kind: "external" });
      }
      edges.push({ from: pid, to: rec.id });
    }
  }

  const trimmed = nodes.length > Math.max(1, maxNodes);
  const visibleNodes = trimmed ? nodes.slice(0, Math.max(1, maxNodes)) : nodes;
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = edges.filter((e) => visibleIds.has(e.from) && visibleIds.has(e.to));

  // REQ-055 item 3f: nodes size to their content (a fixed-width box clips
  // labels like `SEMANTIC_VALUE_DRIFT · 77b78e79`). One uniform width for
  // every node — the widest label — and columns spaced to fit it, so no
  // box overflows and no siblings overlap. Full text rides <title>.
  const nodeW = Math.max(
    NODE_W_MIN,
    ...visibleNodes.map((n) => Math.ceil(16 + Math.max(n.label.length * LABEL_PX, n.sub.length * SUB_PX))),
  );
  const colW = nodeW + COL_GAP;
  const pos = layout(visibleNodes, visibleEdges, colW);
  const maxDepth = Math.max(...[...pos.values()].map((p) => p.depth), 0);
  const perDepth = new Map<number, number>();
  for (const p of pos.values()) perDepth.set(p.depth, (perDepth.get(p.depth) ?? 0) + 1);
  const tallestColumn = Math.max(...perDepth.values(), 1);
  const width = PAD * 2 + maxDepth * colW + nodeW;
  const height = PAD * 2 + tallestColumn * ROW_H;

  const nodeFill = (kind: GraphNode["kind"]) =>
    kind === "drift" ? "var(--band)" : kind === "external" ? "var(--ground)" : "var(--off)";

  return (
    <div data-testid="evidence-graph" className="overflow-x-auto border border-rule bg-ground">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Evidence graph: ${visibleNodes.length} nodes, ${visibleEdges.length} edges`}
      >
        {visibleEdges.map((e, i) => {
          const a = pos.get(e.from);
          const b = pos.get(e.to);
          if (!a || !b) return null;
          const x1 = a.x + nodeW;
          const y1 = a.y + NODE_H / 2;
          const x2 = b.x;
          const y2 = b.y + NODE_H / 2;
          const mx = (x1 + x2) / 2;
          return (
            <path
              key={`${e.from}->${e.to}-${i}`}
              data-testid="evidence-graph-edge"
              d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke="var(--ink-2)"
              strokeWidth="1.5"
            />
          );
        })}
        {visibleNodes.map((n) => {
          const p = pos.get(n.id);
          if (!p) return null;
          return (
            <g key={n.id} data-testid={`evidence-graph-node-${shortId(n.id)}`} data-node-kind={n.kind}>
              <title>{`${n.label} ${n.sub}`}</title>
              <rect
                x={p.x}
                y={p.y}
                width={nodeW}
                height={NODE_H}
                fill={nodeFill(n.kind)}
                stroke={n.kind === "drift" ? "var(--live)" : "var(--rule)"}
                strokeWidth={n.kind === "drift" ? 2 : 1}
              />
              <text x={p.x + 8} y={p.y + 13} fontSize="11" fontFamily="monospace" fill="var(--ink)">
                {n.label}
              </text>
              <text x={p.x + 8} y={p.y + 25} fontSize="10" fontFamily="monospace" fill="var(--ink-2)">
                {n.sub}
              </text>
            </g>
          );
        })}
      </svg>
      {trimmed ? (
        <p data-testid="evidence-graph-more" className="border-t border-rule px-3 py-1.5 text-[11px] text-ink-2 num">
          Showing {visibleNodes.length} of {nodes.length} nodes — narrow the drift selection to see more.
        </p>
      ) : null}
      <ul className="sr-only">
        {visibleNodes.map((n) => (
          <li key={n.id}>
            {n.kind} {n.label} {n.sub}
            {visibleEdges.filter((e) => e.to === n.id).map((e) => ` (from ${shortId(e.from)})`).join("")}
          </li>
        ))}
      </ul>
    </div>
  );
}
