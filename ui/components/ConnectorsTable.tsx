"use client";

import Link from "next/link";
import type { ConnectorOut, ConnectorDetail, ModelUsageResponse } from "@/lib/api/client";
import { HealthBadge } from "./HealthBadge";
import { SyncOutcomeBadge } from "./SyncOutcomeBadge";
import { Sparkline } from "./Sparkline";

export type ConnectorRow = {
  connector: ConnectorOut;
  detail?: ConnectorDetail | null;
};

type Props = {
  connectors: ConnectorOut[];
  detailsById?: Record<string, ConnectorDetail | null>;
  modelUsage?: ModelUsageResponse | null;
};

function driftCount(detail: ConnectorDetail | null | undefined): number {
  if (!detail) return 0;
  return (detail.open_drift ?? []).length;
}

function lastSyncOutcome(detail: ConnectorDetail | null | undefined): string | null {
  return detail?.last_sync?.outcome ?? null;
}

function activeReleaseLabel(connector: ConnectorOut, detail: ConnectorDetail | null | undefined): string {
  if (detail?.releases?.length) {
    const active = detail.releases.find((r) => r.id === connector.active_release_id);
    if (active) return `v${active.version}`;
    // fallback to latest release version if active id missing
    const latest = detail.releases[detail.releases.length - 1];
    return latest ? `v${latest.version}` : "—";
  }
  return connector.active_release_id ? connector.active_release_id.slice(0, 8) : "—";
}

function sparklineValues(modelUsage: ModelUsageResponse | null | undefined, connectorId: string): number[] {
  // Prefer per-run entries if present; else use total as single point so the sparkline is non-empty.
  if (!modelUsage) return [];
  const entries = modelUsage.entries ?? [];
  // If entries carry by_run, try to find ones for this connector (best effort: check run ids or consumer).
  // Fallback: use total_calls split as a tiny series so the UI always shows something when there is usage.
  if (entries.length) {
    const perConnector = entries.flatMap((e) => {
      const runs = (e as unknown as { by_run?: Array<{ connector_id?: string; calls?: number }> }).by_run ?? [];
      return runs.filter((r) => r.connector_id === connectorId).map((r) => r.calls ?? 0);
    });
    if (perConnector.length) return perConnector;
    // No per-connector breakdown: show a 3-point sparkline from total_calls
    const total = modelUsage.total_calls ?? 0;
    if (total > 0) return [Math.max(1, Math.floor(total / 3)), Math.max(1, Math.floor(total / 2)), total];
  }
  if (modelUsage.total_calls > 0) return [modelUsage.total_calls];
  return [];
}

export function ConnectorsTable({ connectors, detailsById, modelUsage }: Props) {
  if (!connectors.length) {
    return <p data-testid="connectors-empty" className="py-8 text-center text-sm text-ink-2">No connectors yet. Create one to get started.</p>;
  }

  return (
    <div className="overflow-x-auto border bg-band">
      <table data-testid="connectors-table" className="w-full text-left text-sm">
        <thead className="bg-ground text-xs uppercase tracking-wide text-ink-2">
          <tr>
            <th className="px-4 py-3 font-medium">Connector</th>
            <th className="px-4 py-3 font-medium">Health</th>
            <th className="px-4 py-3 font-medium">Active release</th>
            <th className="px-4 py-3 font-medium">Last sync</th>
            <th className="px-4 py-3 font-medium">Open drift</th>
            <th className="px-4 py-3 font-medium">Model usage</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {connectors.map((c) => {
            const detail = detailsById?.[c.id] ?? null;
            return (
              <tr key={c.id} data-testid={`connector-row-${c.id}`} className="hover:bg-ground">
                <td className="px-4 py-3">
                  <Link href={`/connectors/${encodeURIComponent(c.id)}`} className="font-medium text-ink hover:underline">
                    {c.name}
                  </Link>
                  <div className="text-xs text-ink-2">{c.base_url}</div>
                  <div className="text-[11px] text-ink-2">{c.id.slice(0, 8)}</div>
                </td>
                <td className="px-4 py-3">
                  <HealthBadge status={c.health_status ?? "unknown"} />
                </td>
                <td className="px-4 py-3 font-mono text-xs">{activeReleaseLabel(c, detail)}</td>
                <td className="px-4 py-3">
                  <SyncOutcomeBadge outcome={lastSyncOutcome(detail)} />
                  {detail?.last_sync?.unmet_criteria?.length ? (
                    <div className="mt-1 text-[11px] text-ink-2">{detail.last_sync.unmet_criteria.join(", ")}</div>
                  ) : null}
                </td>
                <td className="px-4 py-3">
                  {driftCount(detail) > 0 ? (
                    <span
                      data-testid={`drift-count-${c.id}`}
                      className="inline-flex items-center bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                    >
                      {driftCount(detail)} open
                    </span>
                  ) : (
                    <span data-testid={`drift-count-${c.id}`} className="text-xs text-ink-2">0</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Sparkline values={sparklineValues(modelUsage, c.id)} width={80} height={24} label={`model usage for ${c.name}`} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
