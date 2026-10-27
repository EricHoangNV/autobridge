"use client";

import { SyncOutcomeBadge } from "./SyncOutcomeBadge";
import type { SyncRunOut } from "@/lib/api/client";

type Props = {
  syncRuns: SyncRunOut[];
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function SyncRunsTable({ syncRuns }: Props) {
  if (!syncRuns.length) {
    return (
      <div data-testid="sync-runs-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
        No sync runs yet. Trigger a sync to see results.
      </div>
    );
  }

  return (
    <div data-testid="sync-runs-table-wrap" className="overflow-hidden rounded-lg border bg-white">
      <table data-testid="sync-runs-table" className="w-full text-left text-sm">
        <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="px-4 py-2 font-medium">Outcome</th>
            <th className="px-4 py-2 font-medium">Fingerprint</th>
            <th className="px-4 py-2 font-medium">Unmet criteria</th>
            <th className="px-4 py-2 font-medium">Records (obs / emit)</th>
            <th className="px-4 py-2 font-medium">Started</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {syncRuns.map((run) => (
            <tr key={run.id} data-testid={`sync-run-row-${run.id}`} className="hover:bg-zinc-50">
              <td className="px-4 py-2">
                <SyncOutcomeBadge outcome={run.outcome} />
              </td>
              <td className="px-4 py-2 font-mono text-xs">
                {run.fingerprint_id ? (
                  <span data-testid={`fingerprint-${run.id}`} title={run.fingerprint_id}>
                    {run.fingerprint_id.slice(0, 8)}…
                  </span>
                ) : (
                  <span className="text-zinc-400">—</span>
                )}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-600">
                {run.unmet_criteria.length ? (
                  <span data-testid={`unmet-criteria-${run.id}`}>{run.unmet_criteria.join(", ")}</span>
                ) : (
                  <span className="text-zinc-400">—</span>
                )}
              </td>
              <td className="px-4 py-2 font-mono text-xs text-zinc-600">
                {Object.keys(run.records_observed ?? {}).length || Object.keys(run.records_emitted ?? {}).length ? (
                  <span>
                    {JSON.stringify(run.records_observed)} / {JSON.stringify(run.records_emitted)}
                  </span>
                ) : (
                  <span className="text-zinc-400">—</span>
                )}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-600">{formatDate(run.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
