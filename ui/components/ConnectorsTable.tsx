"use client";

import Link from "next/link";
import type {
  ConnectorOut,
  ConnectorDetail,
  DriftEventOut,
  ModelUsageResponse,
  RepairRunOut,
} from "@/lib/api/client";
import { HealthBadge } from "./HealthBadge";
import { ConnectorStateBadge } from "./ConnectorStateBadge";
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
  repairsByConnector?: Record<string, RepairRunOut[]>;
};

export function driftCount(detail: ConnectorDetail | null | undefined): number {
  if (!detail) return 0;
  return (detail.open_drift ?? []).length;
}

function lastSyncOutcome(detail: ConnectorDetail | null | undefined): string | null {
  return detail?.last_sync?.outcome ?? null;
}

function activeReleaseVersion(connector: ConnectorOut, detail: ConnectorDetail | null | undefined): number | null {
  if (detail?.releases?.length) {
    const active = detail.releases.find((r) => r.id === connector.active_release_id);
    if (active) return active.version;
    return detail.releases[detail.releases.length - 1]?.version ?? null;
  }
  return null;
}

/** Newest release that is not the active one: the candidate waiting for approval. */
function candidateVersion(connector: ConnectorOut, detail: ConnectorDetail | null | undefined): number | null {
  if (!detail?.releases?.length) return null;
  const activeId = connector.active_release_id ?? detail.active_release_id;
  const others = detail.releases
    .filter((r) => r.id !== activeId)
    .sort((a, b) => b.version - a.version);
  return others.length ? others[0].version : null;
}
export function frontierTokensOf(repair: RepairRunOut): number {
  const mu = repair.model_usage as Record<string, unknown> | null | undefined;
  const raw = mu?.frontier_tokens;
  const n = typeof raw === "number" ? raw : Number(raw ?? 0);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/** Repairs still waiting for a human (decision == "propose"), newest first. */
export function waitingRepairs(repairs: RepairRunOut[]): RepairRunOut[] {
  return repairs
    .filter((r) => r.decision === "propose")
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
}

/** A repair whose climb is still running (REQ-043), newest first. */
export function climbingRepairs(repairs: RepairRunOut[]): RepairRunOut[] {
  return repairs
    .filter((r) => r.decision === "climbing")
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
}

/** Highest ladder level in a (possibly partial) trace; 0 when empty. */
export function currentRungOfTrace(trace: Array<Record<string, unknown>> | null | undefined): number {
  let best = 0;
  for (const entry of trace ?? []) {
    const level = entry["level"];
    if (typeof level === "number" && Number.isFinite(level) && level > best) best = level;
  }
  return best;
}

/**
 * Attribute repairs to connectors via their drift events.
 * Repairs whose drift ids are unknown (drift fetch failed) are skipped.
 */
export function groupRepairsByConnector(
  repairs: RepairRunOut[],
  drifts: DriftEventOut[],
): Record<string, RepairRunOut[]> {
  const driftToConnector = new Map(drifts.map((d) => [d.id, d.connector_id]));
  const out: Record<string, RepairRunOut[]> = {};
  for (const r of repairs) {
    const connectorIds = new Set(
      (r.drift_event_ids ?? [])
        .map((id) => driftToConnector.get(id))
        .filter((v): v is string => typeof v === "string"),
    );
    for (const cid of connectorIds) {
      (out[cid] ??= []).push(r);
    }
  }
  for (const list of Object.values(out)) {
    list.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
  }
  return out;
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function recordsTotal(lastSync: ConnectorDetail["last_sync"]): number | null {
  if (!lastSync) return null;
  const obs = Object.values((lastSync.records_observed ?? {}) as Record<string, unknown>);
  const nums = obs.filter((v): v is number => typeof v === "number");
  if (!nums.length) return null;
  return nums.reduce((a, b) => a + b, 0);
}

export function ConnectorsTable({ connectors, detailsById, modelUsage: _modelUsage, repairsByConnector }: Props) {
  void _modelUsage;
  if (!connectors.length) {
    return (
      <p data-testid="connectors-empty" className="px-10 py-8 text-center text-sm text-ink-2">
        No connectors yet. Create one to get started.
      </p>
    );
  }

  return (
    <ul data-testid="connectors-table" className="rundown">
      {connectors.map((c) => {
        const detail = detailsById?.[c.id] ?? null;
        const version = activeReleaseVersion(c, detail);
        const outcome = lastSyncOutcome(detail);
        const unmet = detail?.last_sync?.unmet_criteria ?? [];
        const drift = detail?.open_drift ?? [];
        const repairs = repairsByConnector?.[c.id] ?? [];
        const waiting = waitingRepairs(repairs);
        const candidate = candidateVersion(c, detail);
        const needs = drift.length > 0;
        const tokenSeries = repairs.slice(-20).map(frontierTokensOf);
        const tokenTotal = tokenSeries.reduce((a, b) => a + b, 0);
        const total = recordsTotal(detail?.last_sync);
        const syncTime = formatTime(detail?.last_sync?.started_at);
        return (
          <li
            key={c.id}
            data-testid={`connector-row-${c.id}`}
            className={needs ? "crow needs" : "crow"}
          >
            <div className="name">
              <Link href={`/connectors/${encodeURIComponent(c.id)}`} title={c.name}>{c.name}</Link>
              <code>
                {c.id.slice(0, 8)} · {c.base_url}
              </code>
              <HealthBadge status={c.health_status ?? "unknown"} />
              <ConnectorStateBadge
                state={c.state ?? "degrading"}
                activity={c.activity ?? null}
                lastSyncAt={c.last_sync_at ?? null}
              />
            </div>
            <div className="out">
              <span className="clbl">Last sync</span>
              <SyncOutcomeBadge outcome={outcome} />
              {syncTime ? <span className="num text-sm text-ink-2"> {syncTime}</span> : null}
              {outcome ? (
                <p className="num">
                  {unmet.length
                    ? unmet.join(", ")
                    : total !== null
                      ? `${total.toLocaleString()} records, every check passed`
                      : detail?.last_sync
                        ? "ran, no record counts reported"
                        : "no sync yet"}
                </p>
              ) : (
                <p>no sync yet</p>
              )}
            </div>
            <div className="rel">
              <span className="clbl">Release</span>
              <span className="r num">{version !== null ? `r${version}` : "—"}</span>
              {candidate !== null ? <small>r{candidate} waiting</small> : null}
            </div>
            <div className="drift">
              <span className="clbl">Open drift</span>
              <span data-testid={`drift-count-${c.id}`} className="n num">
                {drift.length > 0 ? `${drift.length} open` : "0"}
              </span>
              {drift.length ? <code>{drift[0].drift_class}</code> : null}
              {(() => {
                // REQ-043: the connector row's activity links to the
                // running repair — the break → watch-it-fix demo moment.
                const climbing = climbingRepairs(repairs)[0];
                if (!climbing) return null;
                const rung = Math.max(
                  currentRungOfTrace(climbing.ladder_trace as Array<Record<string, unknown>>),
                  climbing.ladder_level_reached ?? 0,
                );
                return (
                  <Link
                    href={`/repairs/${encodeURIComponent(climbing.id)}`}
                    data-testid={`connector-repair-link-${c.id}`}
                    className="mt-1 inline-block text-xs hover:underline"
                  >
                    repairing at L{rung} →
                  </Link>
                );
              })()}
            </div>
            <div className="cspark">
              <span className="clbl">Model tokens, per repair</span>
              {/* REQ-048 item 4: one repair is a number, not a chart — a
                  lone bar fills the cell and reads as a glitch. Bars only
                  for 2+ repairs. */}
              {repairs.length > 1 ? (
                <Sparkline
                  values={tokenSeries}
                  label={repairs.length ? `frontier tokens for ${c.name}` : `no repairs for ${c.name} yet`}
                />
              ) : null}
              <p className="num" data-testid={`connector-tokens-${c.id}`}>
                {repairs.length ? (
                  <>
                    <b>{tokenTotal.toLocaleString()}</b> in {repairs.length} repair{repairs.length === 1 ? "" : "s"}
                  </>
                ) : (
                  "no repairs yet"
                )}
              </p>
            </div>
            {waiting.length ? (
              <div className="go">
                <Link className="btn" href={`/repairs/${encodeURIComponent(waiting[0].id)}`}>
                  Review repair {waiting[0].id.slice(0, 8)}
                </Link>
                <span className="num text-sm text-ink-2">
                  L{waiting[0].ladder_level_reached} · {frontierTokensOf(waiting[0]).toLocaleString()} frontier tokens ·
                  needs your approval
                </span>
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
