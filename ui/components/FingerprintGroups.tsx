"use client";

import { useState } from "react";
import Link from "next/link";

export type FingerprintRun = {
  id: string;
  fingerprint_id?: string | null;
  outcome: string;
  started_at: string;
  finished_at?: string | null;
};

export type FingerprintGroup = {
  fingerprint: string;
  runs: FingerprintRun[];
  outcome: string;
  startedAt: string;
  endedAt: string;
};

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

/**
 * Group **consecutive** sync runs sharing the same fingerprint.
 * A fingerprint change (drift) always starts a new group, even when the
 * fingerprint value was seen before. Runs without a fingerprint are
 * skipped — they carry no wire shape to compare.
 */
export function groupSyncRunsByFingerprint(runs: FingerprintRun[]): FingerprintGroup[] {
  const ordered = [...runs]
    .filter((r) => r.fingerprint_id)
    .sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
  const groups: FingerprintGroup[] = [];
  for (const run of ordered) {
    const fp = run.fingerprint_id as string;
    const last = groups[groups.length - 1];
    if (last && last.fingerprint === fp) {
      last.runs.push(run);
      last.endedAt = run.started_at;
      if (!last.outcome.split(" / ").includes(run.outcome)) {
        last.outcome = `${last.outcome} / ${run.outcome}`;
      }
    } else {
      groups.push({
        fingerprint: fp,
        runs: [run],
        outcome: run.outcome,
        startedAt: run.started_at,
        endedAt: run.started_at,
      });
    }
  }
  return groups;
}

/** "09:05 → 09:32 PM" — drop the period on the start when both match. */
export function formatRange(startIso: string, endIso: string): string {
  const start = formatTime(startIso);
  const end = formatTime(endIso);
  if (startIso === endIso) return end;
  const m = end.match(/\s*([AP]M)\s*$/i);
  if (m && start.toUpperCase().endsWith(m[1].toUpperCase())) {
    return `${start.slice(0, start.length - m[0].length).trim()} → ${end}`;
  }
  return `${start} → ${end}`;
}

export function fingerprintSummary(groups: FingerprintGroup[]): string {
  if (groups.length === 0) return "No fingerprints recorded yet.";
  if (groups.length === 1) {
    const g = groups[0];
    return `1 fingerprint · ${g.runs.length} sync${g.runs.length === 1 ? "" : "s"}`;
  }
  const lastChange = formatTime(groups[groups.length - 1].startedAt);
  return `${groups.length} fingerprints · last change ${lastChange}`;
}

type DriftRef = {
  driftId: string;
  driftClass: string;
  repairId?: string;
};

type Props = {
  groups: FingerprintGroup[];
  /** Sync-run id → open drift + its repair (for highlighting fingerprint changes). */
  driftBySyncRunId?: Map<string, DriftRef>;
};

export function FingerprintGroups({ groups, driftBySyncRunId }: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggle(index: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  if (!groups.length) {
    return <p className="text-sm text-ink-2">No fingerprints recorded yet.</p>;
  }

  return (
    <ol data-testid="fingerprint-groups" className="fp-groups num">
      {groups.map((group, index) => {
        const open = expanded.has(index);
        const short = group.fingerprint.slice(0, 8);
        const drift = driftBySyncRunId?.get(group.runs[0]?.id ?? "");
        const isChange = index > 0;
        return (
          <li
            key={`${group.fingerprint}-${index}`}
            data-testid={`fingerprint-group-${index}`}
            className={isChange ? "fp-change" : undefined}
          >
            <button
              type="button"
              data-testid={`fingerprint-group-toggle-${index}`}
              aria-expanded={open}
              onClick={() => toggle(index)}
              className="fp-row"
            >
              <span aria-hidden="true">{open ? "▾" : "▸"}</span>{" "}
              <code
                tabIndex={0}
                title={group.fingerprint}
                aria-label={`fingerprint ${group.fingerprint}`}
              >
                {short}…
              </code>
              <span>
                {" "}· {group.runs.length} sync{group.runs.length === 1 ? "" : "s"} · {group.outcome} ·{" "}
                {formatRange(group.startedAt, group.endedAt)}
              </span>
            </button>
            {isChange ? (
              <span className="fp-changed-note">
                {" "}changed at {formatTime(group.startedAt)}
                {drift ? (
                  <>
                    {" "}— drift{" "}
                    <code tabIndex={0} title={drift.driftId}>
                      {drift.driftId.slice(0, 8)}
                    </code>
                    {drift.repairId ? (
                      <>
                        {" "}·{" "}
                        <Link
                          data-testid={`fingerprint-group-drift-link-${index}`}
                          href={`/repairs/${encodeURIComponent(drift.repairId)}`}
                          className="link"
                        >
                          view repair
                        </Link>
                      </>
                    ) : null}
                  </>
                ) : null}
              </span>
            ) : null}
            {open ? (
              <ul className="fp-runs">
                {group.runs.map((run) => (
                  <li key={run.id} data-testid={`fingerprint-entry-${run.id}`}>
                    <code tabIndex={0} title={run.id} aria-label={`sync run ${run.id}`}>
                      {run.id.slice(0, 8)}
                    </code>{" "}
                    · {formatTime(run.started_at)} · {run.outcome}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
