"use client";

import { Fragment } from "react";
import { SyncOutcomeBadge } from "./SyncOutcomeBadge";
import type { SyncRunOut } from "@/lib/api/client";

type Props = {
  syncRuns: SyncRunOut[];
  /** Edge (fias_tcp) connectors show the persisted link note (REQ-071);
   * other connectors' rows are unchanged. */
  isEdge?: boolean;
};

/** The per-sync link snapshot REQ-071 records on data_readiness. */
type EdgeLink = {
  link_up?: boolean;
  reconnects?: number;
  posting_keys_total?: number;
  posting_keys_unique?: number;
  posting_keys?: string[];
  posting_keys_truncated?: boolean;
};

function edgeLinkOf(run: SyncRunOut): EdgeLink | null {
  const readiness = (run.data_readiness ?? {}) as Record<string, unknown>;
  const link = readiness["edge_link"];
  return link !== null && typeof link === "object" ? (link as EdgeLink) : null;
}

function recordsTotalNum(run: SyncRunOut): number {
  const obs = Object.values((run.records_observed ?? {}) as Record<string, unknown>);
  return obs.reduce<number>((sum, v) => (typeof v === "number" ? sum + v : sum), 0);
}

/** Lost postings between two syncs, from the relay's idempotency keys
 * (REQ-071): keys present before but gone now. Falls back to the stored
 * unique counts when a key list was truncated; 0 when unknowable. */
function lostFromKeys(prev: EdgeLink | null, curr: EdgeLink): number {
  if (
    prev &&
    Array.isArray(prev.posting_keys) &&
    Array.isArray(curr.posting_keys) &&
    !curr.posting_keys_truncated
  ) {
    const now = new Set(curr.posting_keys);
    let lost = 0;
    for (const key of prev.posting_keys) {
      if (!now.has(key)) lost += 1;
    }
    return lost;
  }
  if (typeof prev?.posting_keys_unique === "number" && typeof curr.posting_keys_unique === "number") {
    return Math.max(0, prev.posting_keys_unique - curr.posting_keys_unique);
  }
  return 0;
}

/** The persisted link note for one row (REQ-071), or null for no note.
 * `rows` is newest-first (API order); the previous sync of the same
 * connector is the next older row. The reconnect counter — not the link
 * state at sync time — drives the drop note, so short drops between two
 * syncs still show. */
export function edgeLinkNote(
  run: SyncRunOut,
  prev: SyncRunOut | null,
  isEdge: boolean | undefined,
): string | null {
  if (!isEdge) return null;
  const link = edgeLinkOf(run);
  if (!link) return null;
  if (prev) {
    const prevLink = edgeLinkOf(prev);
    if (
      typeof link.reconnects === "number" &&
      prevLink &&
      typeof prevLink.reconnects === "number" &&
      link.reconnects > prevLink.reconnects
    ) {
      const delta = Math.max(0, recordsTotalNum(run) - recordsTotalNum(prev));
      const duplicated = Math.max(
        0,
        (link.posting_keys_total ?? 0) - (link.posting_keys_unique ?? 0),
      );
      const lost = lostFromKeys(prevLink, link);
      return `link dropped, re-handshaked · +${delta} postings, ${lost} lost, ${duplicated} duplicated`;
    }
  }
  if (link.link_up === false) return "link down";
  return null;
}

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

export function SyncRunsTable({ syncRuns, isEdge }: Props) {
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
            <th
              className="r"
              tabIndex={0}
              title="Shape of the vendor's responses; a change means drift."
            >
              Fingerprint
            </th>
          </tr>
        </thead>
        <tbody>
          {syncRuns.map((run, index) => {
            // REQ-073 item 7: the drop note gets a full-width line under
            // the row — never squeezed into the narrow Outcome column —
            // while Records/Fingerprint stay aligned on the data row.
            const note = edgeLinkNote(run, index + 1 < syncRuns.length ? syncRuns[index + 1] : null, isEdge);
            return (
              <Fragment key={run.id}>
                <tr data-testid={`sync-run-row-${run.id}`}>
                  <td>
                    <span tabIndex={0} title={run.id}>
                      {run.id.slice(0, 8)}
                    </span>{" "}
                    · {formatDate(run.started_at)}
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
                      <span data-testid={`fingerprint-${run.id}`} tabIndex={0} title={run.fingerprint_id}>
                        <code>
                          {run.fingerprint_id.slice(0, 8)}…
                        </code>
                      </span>
                    ) : (
                      <span className="text-ink-2">—</span>
                    )}
                  </td>
                </tr>
                {note ? (
                  <tr data-testid={`sync-run-note-row-${run.id}`}>
                    <td colSpan={5} className="link-note-cell">
                      <div data-testid={`link-note-${run.id}`} className="text-ink-2">
                        {note}
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
