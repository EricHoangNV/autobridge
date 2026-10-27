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
      <div data-testid="sync-runs-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
        No sync runs yet. Trigger a sync to see results.
      </div>
    );
  }

  return (
    <div data-testid="sync-runs-table-wrap" className="overflow-hidden border bg-band">
      <table data-testid="sync-runs-table" className="w-full text-left text-sm">
        <thead className="bg-ground text-xs uppercase tracking-wide text-ink-2">
          <tr>
            <th className="px-4 py-2 font-medium">Outcome</th>
            <th className="px-4 py-2 font-medium">Fingerprint</th>
            <th className="px-4 py-2 font-medium">Unmet criteria</th>
            <th className="px-4 py-2 font-medium">Records (obs / emit)</th>
            <th className="px-4 py-2 font-medium">Started</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {syncRuns.map((run) => (
            <tr key={run.id} data-testid={`sync-run-row-${run.id}`} className="hover:bg-ground">
              <td className="px-4 py-2">
                <SyncOutcomeBadge outcome={run.outcome} />
              </td>
              <td className="px-4 py-2 font-mono text-xs">
                {run.fingerprint_id ? (
                  <span data-testid={`fingerprint-${run.id}`} title={run.fingerprint_id}>
                    {run.fingerprint_id.slice(0, 8)}…
                  </span>
                ) : (
                  <span className="text-ink-2">—</span>
                )}
              </td>
              <td className="px-4 py-2 text-xs text-ink-2">
                {run.unmet_criteria.length ? (
                  <span data-testid={`unmet-criteria-${run.id}`}>{run.unmet_criteria.join(", ")}</span>
                ) : (
                  <span className="text-ink-2">—</span>
                )}
              </td>
              <td className="px-4 py-2 font-mono text-xs text-ink-2">
                {Object.keys(run.records_observed ?? {}).length || Object.keys(run.records_emitted ?? {}).length ? (
                  <span>
                    {JSON.stringify(run.records_observed)} / {JSON.stringify(run.records_emitted)}
                  </span>
                ) : (
                  <span className="text-ink-2">—</span>
                )}
              </td>
              <td className="px-4 py-2 text-xs text-ink-2">{formatDate(run.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
