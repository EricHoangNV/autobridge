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

type ConnectorLite = {
  id: string;
  active_release_id?: string | null;
  state?: string | null;
  activity?: string | null;
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

type Step = { key: string; label: string; done: boolean; detail: string | null };

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
  if (!approval || approval.decision !== "approve") return null;

  const candidateId = approval.to_release_id ?? null;
  const activeId = connector?.active_release_id ?? null;
  const activated = candidateId !== null && activeId === candidateId;
  const verifying = (syncRuns ?? []).filter((s) => (candidateId ? s.release_id === candidateId : true));
  const latest = verifying.length
    ? [...verifying].sort((a, b) => (a.started_at < b.started_at ? 1 : -1))[0]
    : null;
  const complete = latest?.outcome === "Complete";
  const working = (connector?.state ?? "") === "working";

  const steps: Step[] = [
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
      detail: activated && candidateId ? `r is active (${candidateId.slice(0, 8)})` : "waiting for activation",
    },
    {
      key: "sync",
      label: "Verifying sync",
      done: latest !== null,
      detail: latest ? `${latest.outcome}${latest.unmet_criteria?.length ? `: ${latest.unmet_criteria.join(", ")}` : ""}` : "waiting for the next sync",
    },
    {
      key: "complete",
      label: "Sync Complete",
      done: complete,
      detail: latest && !complete ? `latest sync is ${latest.outcome}` : null,
    },
    {
      key: "working",
      label: "Connector working",
      done: working,
      detail: connector?.activity ?? null,
    },
  ];

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
