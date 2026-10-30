"use client";

import { SyncOutcomeBadge } from "./SyncOutcomeBadge";
import type { SyncRunOut } from "@/lib/api/client";

type Props = {
  syncRuns: SyncRunOut[];
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const now = new Date();
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return `today ${time}`;
    return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
  } catch {
    return iso;
  }
}

function recordsTotal(run: SyncRunOut): string {
  const obs = Object.values((run.records_observed ?? {}) as Record<string, unknown>);
  const nums = obs.filter((v): v is number => typeof v === "number");
  if (!nums.length) return "—";
  return nums.reduce((a, b) => a + b, 0).toLocaleString();
}

export function SyncRunsTable({ syncRuns }: Props) {
  if (!syncRuns.length) {
    return (
      <div data-testid="sync-runs-empty" className="border border-rule px-4 py-6 text-sm text-ink-2">
        <span data-testid="no-sync-runs">No sync runs yet. Trigger a sync to see results.</span>
      </div>
    );
  }

  return (
    <div data-testid="sync-runs-table-wrap" className="syncruns-scroll">
      <table data-testid="sync-runs-table" className="ctab num">
        <thead>
          <tr>
            <th>Run</th>
            <th>Outcome</th>
            <th className="hide-s">Unmet criterion</th>
            <th className="r">Records</th>
            <th className="r">Fingerprint</th>
          </tr>
        </thead>
        <tbody>
          {syncRuns.map((run) => (
            <tr key={run.id} data-testid={`sync-run-row-${run.id}`}>
              <td>
                {run.id.slice(0, 8)} · {formatDate(run.started_at)}
              </td>
              <td>
                <SyncOutcomeBadge outcome={run.outcome} />
              </td>
              <td className="hide-s">
                {run.unmet_criteria.length ? (
                  <span data-testid={`unmet-criteria-${run.id}`}>
                    <code>{run.unmet_criteria.join(", ")}</code>
                  </span>
                ) : (
                  <span className="text-ink-2">—</span>
                )}
              </td>
              <td className="r">{recordsTotal(run)}</td>
              <td className="r">
                {run.fingerprint_id ? (
                  <span data-testid={`fingerprint-${run.id}`} title={run.fingerprint_id}>
                    <code>
                      {run.fingerprint_id.slice(0, 8)}…
                    </code>
                  </span>
                ) : (
                  <span className="text-ink-2">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
