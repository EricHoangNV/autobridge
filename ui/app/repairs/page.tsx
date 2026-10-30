"use client";

import useSWR from "swr";
import Link from "next/link";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";
import type {
  ConnectorOut,
  DriftEventOut,
  RepairRunOut,
} from "@/lib/api/client";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl();
  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

export type RepairStatus = "fix ready" | "needs your decision" | "approved" | "applied" | "rejected" | "rolled back" | "repairing";

/**
 * Highest ladder level in a (possibly partial) trace; 0 when empty.
 * While a repair climbs, the row's ladder_level_reached stays 0 until
 * the final write — the trace carries the rung it is on (REQ-043).
 */
export function currentRungOf(repair: RepairRunOut): number {
  let best = 0;
  for (const entry of repair.ladder_trace ?? []) {
    const level = (entry as Record<string, unknown>)["level"];
    if (typeof level === "number" && Number.isFinite(level) && level > best) best = level;
  }
  return Math.max(best, repair.ladder_level_reached ?? 0);
}

function toNumber(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

export function modelCallsOf(repair: RepairRunOut): { calls: number; frontierTokens: number } {
  const mu = (repair.model_usage ?? {}) as Record<string, unknown>;
  const calls =
    toNumber(mu["calls"] ?? mu["total_calls"]) ||
    toNumber(mu["frontier_calls"]) + toNumber(mu["small_calls"]);
  const frontierTokens = toNumber(mu["frontier_tokens"] ?? mu["frontier_tokens_total"]);
  return { calls, frontierTokens };
}

/**
 * In-flight model calls on a running repair (REQ-048 item 5): the rung
 * snapshot stamps the model id + call start just before a call goes out
 * (REQ-043), so a climbing row whose usage is still 0 reads
 * "N call(s) in flight" instead of "0 model calls" until the call lands.
 */
export function inFlightCallsOf(repair: RepairRunOut): number {
  if (repair.decision !== "climbing") return 0;
  let n = 0;
  for (const entry of repair.ladder_trace ?? []) {
    const rec = entry as Record<string, unknown>;
    if (typeof rec["model"] === "string" && typeof rec["call_started_at"] === "string") n += 1;
  }
  return n;
}

/**
 * Live repair status (REQ-032 §2), from persisted rows only:
 * rejected (decision or reject approval) · rolled back (decision) ·
 * applied (approved release is active) · approved (approved but superseded) ·
 * fix ready (verified patch waiting) · repairing (the climb is still
 * running, REQ-043) · needs your decision (escalated, failed, or still
 * climbing without a row).
 *
 * REQ-053 item 3: the decision is the source of truth — the server moves
 * it terminal on every approval, so status reads the decision first and
 * the approval row second (they agree; the decision wins).
 */
export function repairStatus(
  repair: RepairRunOut,
  activeReleaseByConnector: Record<string, string | null>,
  connectorByDrift: Map<string, string>,
): RepairStatus {
  if (repair.decision === "reject") return "rejected";
  if (repair.decision === "rolled_back") return "rolled back";
  const approval = repair.approval;
  if (approval?.decision === "reject") return "rejected";
  if (repair.decision === "approved" || approval?.decision === "approve") {
    const toRelease = approval?.to_release_id ?? null;
    const applied =
      toRelease !== null && Object.values(activeReleaseByConnector).some((id) => id === toRelease);
    return applied ? "applied" : "approved";
  }
  // REQ-043: a running repair is neither ready nor waiting — it is
  // climbing, with the rung it is on. Failed climbs land back here as
  // needing a decision (the trace names the crash).
  if (repair.decision === "climbing") return "repairing";
  if (repair.decision === "escalate" || repair.decision === "failed") return "needs your decision";
  if (repair.verifier_report_id || (repair.candidate_patch ?? []).length > 0) return "fix ready";
  void connectorByDrift;
  return "needs your decision";
}

function statusClass(status: RepairStatus): string {
  if (status === "fix ready") return "bg-emerald-100 text-emerald-800 border-emerald-200";
  if (status === "needs your decision") return "bg-band text-stop border-stop";
  if (status === "repairing") return "bg-live text-ink border-live";
  if (status === "applied") return "bg-ground text-ink border-rule";
  if (status === "approved") return "bg-ground text-ink-2 border-rule";
  return "bg-ground text-ink-2 border-rule";
}

function levelBadge(level: number) {
  const labels: Record<number, string> = { 0: "L0", 1: "L1", 2: "L2", 3: "L3", 4: "L4" };
  return labels[level] ?? `L${level}`;
}

export default function RepairsPage() {
  const { data: repairs, error, isLoading } = useSWR<RepairRunOut[]>("/repairs", fetcher, {
    refreshInterval: 2000,
  });
  const { data: drifts } = useSWR<DriftEventOut[]>("/drift", fetcher, { refreshInterval: 2000 });
  const { data: connectors } = useSWR<ConnectorOut[]>("/connectors", fetcher, { refreshInterval: 2000 });

  if (isLoading) {
    return (
      <div data-testid="repairs-page" className="py-8 text-center text-sm text-ink-2">
        Loading repairs…
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="repairs-page">
        <div data-testid="repairs-error" className="border border-stop bg-band px-4 py-3 text-sm text-stop">
          Failed to load repairs: {(error as Error).message}
        </div>
      </div>
    );
  }

  const driftToConnector = new Map((drifts ?? []).map((d) => [d.id, d.connector_id]));
  const connectorById = new Map((connectors ?? []).map((c) => [c.id, c]));
  const activeReleaseByConnector: Record<string, string | null> = Object.fromEntries(
    (connectors ?? []).map((c) => [c.id, c.active_release_id ?? null]),
  );
  const sorted = [...(repairs ?? [])].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  return (
    <div data-testid="repairs-page">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Repairs</h1>
          <p className="mt-1 text-sm text-ink-2">
            Ladder trace, patch, tests, verifier verdict, model usage — approve, reject or rollback.
          </p>
        </div>
        <a href="/connectors" className="text-xs text-ink-2 hover:text-ink hover:underline">
          ← Connectors
        </a>
      </div>

      {!sorted.length ? (
        <div data-testid="repairs-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
          No repairs yet. Inject a fault or trigger a sync that drifts to create one.
        </div>
      ) : (
        <div data-testid="repairs-list" className="overflow-hidden border bg-band">
          <table className="w-full text-left text-sm">
            <thead className="bg-ground text-xs uppercase tracking-wide text-ink-2">
              <tr>
                <th className="px-4 py-2 font-medium">Repair</th>
                <th className="px-4 py-2 font-medium">Connector</th>
                <th className="px-4 py-2 font-medium">Drift class</th>
                <th className="px-4 py-2 font-medium">Rung</th>
                <th className="px-4 py-2 font-medium">Rule</th>
                <th className="px-4 py-2 font-medium">Model calls</th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {sorted.map((r) => {
                const connectorIds = [...new Set(
                  (r.drift_event_ids ?? [])
                    .map((id) => driftToConnector.get(id))
                    .filter((v): v is string => typeof v === "string"),
                )];
                const connectorNames = connectorIds.map(
                  (id) => connectorById.get(id)?.name ?? id.slice(0, 8),
                );
                const status = repairStatus(r, activeReleaseByConnector, driftToConnector);
                const { calls, frontierTokens } = modelCallsOf(r);
                const inFlight = inFlightCallsOf(r);
                return (
                  <tr key={r.id} data-testid={`repair-row-${r.id}`} className="hover:bg-ground">
                    <td className="px-4 py-3">
                      <Link href={`/repairs/${encodeURIComponent(r.id)}`} className="font-mono text-xs font-medium text-ink hover:underline">
                        {r.id.slice(0, 8)}
                      </Link>
                      <div className="font-mono text-[11px] text-ink-2">{r.id.slice(0, 12)}…</div>
                    </td>
                    <td data-testid={`repair-connector-${r.id}`} className="px-4 py-3 text-xs">
                      {connectorNames.length ? connectorNames.join(", ") : "—"}
                    </td>
                    <td data-testid={`repair-class-${r.id}`} className="px-4 py-3 font-mono text-xs text-ink-2">
                      {r.drift_class ?? "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span data-testid={`repair-ladder-${r.id}`} className="inline-flex bg-ground px-2 py-0.5 text-xs font-medium text-ink-2">
                        {levelBadge(currentRungOf(r))}{status === "repairing" ? " · running" : ""}
                      </span>
                    </td>
                    <td data-testid={`repair-rule-${r.id}`} className="px-4 py-3 font-mono text-xs text-ink-2">
                      {r.rule_id ?? "—"}
                    </td>
                    <td data-testid={`repair-calls-${r.id}`} className="num px-4 py-3 text-xs text-ink-2">
                      {inFlight > 0
                        ? `${inFlight} call${inFlight === 1 ? "" : "s"} in flight`
                        : calls === 0
                          ? "0 model calls"
                          : `${calls} calls · ${frontierTokens.toLocaleString("en-US")} frontier`}
                    </td>
                    <td className="px-4 py-3">
                      <span data-testid={`repair-status-${r.id}`} className={`inline-flex border px-2 py-0.5 text-xs font-medium ${statusClass(status)}`}>
                        {status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p data-testid="repairs-count" className="num mt-3 text-xs text-ink-2">
        {sorted.length} repair{sorted.length === 1 ? "" : "s"} · newest first · polling every 2s
      </p>
    </div>
  );
}
