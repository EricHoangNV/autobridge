"use client";

import useSWR from "swr";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl().replace(/\/$/, "");
  const res = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

type ApprovalSummary = {
  id: string;
  actor: string;
  decision: string;
  from_release_id?: string | null;
  to_release_id?: string | null;
  created_at: string;
} | null;

type TrackerRelease = {
  id: string;
  version: number;
  provenance_repair_run_id?: string | null;
};

type ConnectorLite = {
  id: string;
  active_release_id?: string | null;
  state?: string | null;
  activity?: string | null;
  releases?: TrackerRelease[] | null;
};

type SyncLite = {
  id: string;
  release_id: string;
  outcome: string;
  unmet_criteria?: string[];
  started_at: string;
};

type RepairLite = {
  id: string;
  approval?: ApprovalSummary;
};

export type TrackerStep = { key: string; label: string; done: boolean; detail: string | null };

/**
 * REQ-073 item 4 / REQ-074 item 1: every step derives from the real
 * records — the approval row, the connector's active release, and the
 * sync runs on the new release — so a shipped fix shows all steps done.
 * A candidate release that went live and was later superseded (e.g.
 * Activating r1 for a second break) still reads done: a sync ran on it,
 * so it was active. When the sync history no longer lists those runs
 * but the release versions prove it (active moved past the candidate),
 * the steps still read done with a "replaced by rN" line — never
 * "waiting for activation" on a release that already went live.
 */
function shortClock(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const d = new Date(t);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}
export function deriveTrackerSteps({
  repairId,
  approval,
  connector,
  syncRuns,
}: {
  repairId: string;
  approval: ApprovalSummary;
  connector: ConnectorLite | null | undefined;
  syncRuns: SyncLite[] | null | undefined;
}): { steps: TrackerStep[]; approved: boolean } {
  if (!approval || approval.decision !== "approve") return { steps: [], approved: false };

  const releases = connector?.releases ?? [];
  const versionOf = (id: string | null): number | null => {
    if (!id) return null;
    const found = releases.find((r) => r.id === id);
    return found ? found.version : null;
  };
  // The candidate is the approval's to_release; older approvals that never
  // recorded one fall back to the release provenanced to this repair.
  const candidateId =
    approval.to_release_id ??
    releases.find((r) => r.provenance_repair_run_id === repairId)?.id ??
    null;
  const candidateLabel = (() => {
    const v = versionOf(candidateId);
    if (v !== null) return `r${v}`;
    return candidateId ? candidateId.slice(0, 8) : "the new release";
  })();
  const activeId = connector?.active_release_id ?? null;
  // First sync on the new release (ascending): the verifying sync and its
  // outcome. Any sync with outcome Complete marks the sync step done.
  const onCandidate = (syncRuns ?? [])
    .filter((s) => candidateId !== null && s.release_id === candidateId)
    .sort((a, b) => (a.started_at < b.started_at ? -1 : a.started_at > b.started_at ? 1 : 0));
  const firstVerifying = onCandidate.length ? onCandidate[0] : null;
  const firstComplete = onCandidate.find((s) => s.outcome === "Complete") ?? null;
  const candidateVersion = versionOf(candidateId);
  const activeVersion = versionOf(activeId);
  // REQ-074 item 1: versions prove a superseded candidate went live even
  // when its sync rows have fallen out of the history.
  const supersededByVersion =
    candidateVersion !== null && activeVersion !== null && activeVersion > candidateVersion;
  const replacedByLabel =
    activeId !== null && activeId !== candidateId
      ? (() => {
          const v = versionOf(activeId);
          return v !== null ? `r${v}` : activeId.slice(0, 8);
        })()
      : null;
  const liveWindow = (() => {
    if (!onCandidate.length) return null;
    const first = shortClock(onCandidate[0].started_at);
    const last = shortClock(onCandidate[onCandidate.length - 1].started_at);
    if (!first) return null;
    return last && last !== first ? `${first}–${last}` : first;
  })();
  const activated = candidateId !== null && (activeId === candidateId || onCandidate.length > 0 || supersededByVersion);
  const syncDone = firstVerifying !== null || supersededByVersion;
  // A superseded candidate with no surviving sync rows must still have
  // run before it was replaced; when rows survive, they decide.
  const complete = firstComplete !== null || (supersededByVersion && onCandidate.length === 0);
  const working = (connector?.state ?? "") === "working";
  const activeLabel = (() => {
    const v = versionOf(activeId);
    return v !== null ? `r${v}` : activeId ? activeId.slice(0, 8) : null;
  })();

  const steps: TrackerStep[] = [
    {
      key: "approval",
      label: "Approval recorded",
      done: true,
      detail: `by ${approval.actor}`,
    },
    {
      key: "activated",
      label: "Release activated",
      done: activated,
      detail:
        activeId === candidateId && candidateId !== null
          ? `${candidateLabel} is active`
          : activated && replacedByLabel
            ? liveWindow
              ? `${candidateLabel} went live ${liveWindow}, replaced by ${replacedByLabel}`
              : `${candidateLabel} went live, replaced by ${replacedByLabel}`
            : activated
              ? `${candidateLabel} went live${activeLabel ? ` (now on ${activeLabel})` : ""}`
              : "waiting for activation",
    },
    {
      key: "sync",
      label: "Verifying sync",
      done: syncDone,
      detail: firstVerifying
        ? `${firstVerifying.outcome}${firstVerifying.unmet_criteria?.length ? `: ${firstVerifying.unmet_criteria.join(", ")}` : ""}`
        : supersededByVersion
          ? `ran before it was replaced by ${replacedByLabel ?? "a later release"}`
          : "waiting for the next sync",
    },
    {
      key: "complete",
      label: "Sync Complete",
      done: complete,
      detail:
        firstComplete !== null
          ? null
          : firstVerifying
            ? `latest sync on ${candidateLabel} is ${firstVerifying.outcome}`
            : null,
    },
    {
      key: "working",
      label: "Connector working",
      done: working,
      detail: connector?.activity ?? null,
    },
  ];
  return { steps, approved: true };
}

/**
 * After-approve progress (REQ-032 §4): follows the outcome live —
 * approval recorded → release activated → verifying sync → Complete →
 * connector working — polling the same rows the engine writes. Renders
 * nothing until an approval exists.
 */
export function AfterApproveTracker({
  repairId,
  connectorId,
}: {
  repairId: string;
  connectorId: string | null;
}) {
  const { data: repair } = useSWR<RepairLite>(`/repairs/${encodeURIComponent(repairId)}`, fetcher, {
    refreshInterval: 2000,
  });
  const { data: connector } = useSWR<ConnectorLite>(
    connectorId ? `/connectors/${encodeURIComponent(connectorId)}` : null,
    fetcher,
    { refreshInterval: 2000 },
  );
  const { data: syncRuns } = useSWR<SyncLite[]>(
    connectorId ? `/connectors/${encodeURIComponent(connectorId)}/sync-runs` : null,
    fetcher,
    { refreshInterval: 2000 },
  );

  const approval = repair?.approval ?? null;
  const { steps, approved } = deriveTrackerSteps({ repairId, approval, connector, syncRuns });
  if (!approved) return null;

  return (
    <ol data-testid="after-approve-tracker" className="mt-3 list-none space-y-1 p-0 text-sm">
      {steps.map((s) => (
        <li key={s.key} data-testid={`after-approve-step-${s.key}`} data-done={s.done ? "yes" : "no"}>
          <span aria-hidden="true">{s.done ? "✓" : "○"}</span> {s.label}
          {s.detail ? <span className="text-ink-2"> — {s.detail}</span> : null}
        </li>
      ))}
    </ol>
  );
}
