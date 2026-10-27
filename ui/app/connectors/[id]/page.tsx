"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import Link from "next/link";
import { SpecViewer } from "@/components/SpecViewer";
import { ReleaseHistory } from "@/components/ReleaseHistory";
import { SyncRunsTable } from "@/components/SyncRunsTable";
import { DependencyList } from "@/components/DependencyList";
import { frontierTokensOf, waitingRepairs } from "@/components/ConnectorsTable";
import type {
  ConnectorDetail,
  ReleaseOut,
  RepairRunOut,
  SyncRunOut,
  DependencyOut,
} from "@/lib/api/client";

function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
}

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl();
  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

type PageProps = {
  params: Promise<{ id: string }> | { id: string };
};

function formatDayTime(iso: string): string {
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

/** Read a nested path from the free-form spec map without assuming its shape. */
function specPath(spec: unknown, path: string[]): unknown {
  let cur: unknown = spec;
  for (const key of path) {
    if (cur === null || typeof cur !== "object" || Array.isArray(cur)) return undefined;
    cur = (cur as Record<string, unknown>)[key];
  }
  return cur;
}

/** Quiet fact segments for the masthead, only from data the spec actually carries. */
function specFacts(spec: ReleaseOut["spec"] | null | undefined): string[] {
  if (!spec || typeof spec !== "object") return [];
  const out: string[] = [];
  const authType = specPath(spec, ["source", "auth", "type"]);
  if (typeof authType === "string" && authType) out.push(`${authType} auth`);
  const pagination = specPath(spec, ["source", "pagination", "strategy"]);
  if (typeof pagination === "string" && pagination) out.push(`${pagination} pagination`);
  const tz = specPath(spec, ["source", "timezone"]);
  if (typeof tz === "string" && tz) out.push(`Dates in ${tz}`);
  return out;
}

export default function ConnectorDetailPage({ params }: PageProps) {
  // Next.js 15: params is Promise<{ id: string }> in production.
  // For test compat we handle both Promise and plain object.
  const [connectorId, setConnectorId] = useState(() => {
    if (params !== null && typeof params === "object" && "then" in (params as Record<string, unknown>)) {
      return "";
    }
    return decodeURIComponent((params as { id: string }).id);
  });

  useEffect(() => {
    if (params !== null && typeof params === "object" && "then" in (params as Record<string, unknown>)) {
      (params as Promise<{ id: string }>).then((v) => setConnectorId(decodeURIComponent(v.id)));
    }
  }, [params]);

  const {
    data: detail,
    error: detailError,
    isLoading: detailLoading,
    mutate: mutateDetail,
  } = useSWR<ConnectorDetail>(`/connectors/${encodeURIComponent(connectorId)}`, fetcher, {
    refreshInterval: 5000,
  });

  // Sync runs history (all runs for this connector's releases)
  const { data: syncRuns, mutate: mutateSyncRuns } = useSWR<SyncRunOut[]>(
    detail ? `/connectors/${encodeURIComponent(connectorId)}/sync-runs` : null,
    fetcher,
    { refreshInterval: 5000 },
  );

  // Repairs for this connector (client-side match on open drift ids, so no
  // server-side filter is assumed): drives the waiting band.
  const { data: allRepairs } = useSWR<RepairRunOut[]>("/repairs", fetcher, {
    refreshInterval: 5000,
  });

  const [syncState, setSyncState] = useState<"idle" | "sending" | "done" | "error">("idle");
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  async function handleSync() {
    setSyncState("sending");
    setSyncMsg(null);
    try {
      const base = getBaseUrl().replace(/\/$/, "");
      const res = await fetch(`${base}/connectors/${encodeURIComponent(connectorId)}/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Sync failed (${res.status}): ${body}`);
      }
      const data = (await res.json()) as { sync_run_id?: string; job_id?: string };
      setSyncState("done");
      setSyncMsg(`Sync started${data.sync_run_id ? ` — run ${data.sync_run_id.slice(0, 8)}` : ""}${data.job_id ? ` (job ${data.job_id.slice(0, 8)})` : ""}`);
      // Refresh
      mutateDetail();
      mutateSyncRuns();
    } catch (err) {
      setSyncState("error");
      setSyncMsg(err instanceof Error ? err.message : String(err));
    }
  }

  // Derive active release spec for viewer
  const activeRelease: ReleaseOut | null = (() => {
    if (!detail?.releases?.length) return null;
    if (detail.active_release_id) {
      const found = detail.releases.find((r) => r.id === detail.active_release_id);
      if (found) return found;
    }
    // Fallback to latest
    return detail.releases[detail.releases.length - 1] ?? null;
  })();

  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(null);
  // Sync selected with active when detail loads and no selection yet
  const specRelease: ReleaseOut | null = (() => {
    if (selectedReleaseId) {
      return detail?.releases?.find((r) => r.id === selectedReleaseId) ?? activeRelease;
    }
    return activeRelease;
  })();

  if (detailLoading) {
    return (
      <div data-testid="connector-detail-page" className="px-10 py-8 text-center text-sm text-ink-2">
        Loading connector…
      </div>
    );
  }

  if (detailError) {
    return (
      <div data-testid="connector-detail-page">
        <a href="/connectors" data-testid="back-to-connectors" className="link px-10">
          ← Back to connectors
        </a>
        <div
          data-testid="connector-detail-error"
          className="mx-10 mt-4 border border-stop bg-band px-4 py-3 text-sm text-stop"
        >
          Failed to load connector {(detailError as Error).message}
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div data-testid="connector-detail-page" className="px-10 py-8 text-center text-sm text-ink-2">
        Connector not found.
      </div>
    );
  }

  const openDrift = detail.open_drift ?? [];
  const openDriftIds = new Set(openDrift.map((d) => d.id));
  const myRepairs = (allRepairs ?? []).filter((r) =>
    (r.drift_event_ids ?? []).some((id) => openDriftIds.has(id)),
  );
  const waiting = waitingRepairs(myRepairs);
  const waitingRepair = waiting[0] ?? null;

  const maxVersion = detail.releases?.length
    ? Math.max(...detail.releases.map((r) => r.version))
    : null;
  const facts = specFacts(specRelease?.spec);
  const entityNames = (detail.target_entities ?? []).join(", ") || "—";

  return (
    <div data-testid="connector-detail-page">
      <section className="mast">
        <div className="crumb">
          <Link href="/connectors">Connectors</Link> / {detail.id.slice(0, 8)}
        </div>
        <div>
          <h1 data-testid="connector-name">{detail.name}</h1>
        </div>
        <p className="facts">
          {facts.map((f) => (
            <span key={f}>
              <b>{f.split(" ")[0]}</b> {f.split(" ").slice(1).join(" ")}
            </span>
          ))}
          <span>
            <b>{(detail.target_entities ?? []).length}</b> entities
          </span>
          <span>
            Credential <b data-testid="connector-credential-ref">{detail.credential_ref}</b>
          </span>
          <span>
            Entities <b data-testid="connector-target-entities">{entityNames}</b>
          </span>
          <span>
            Open drift <b data-testid="connector-open-drift-count">{openDrift.length} open</b>
          </span>
        </p>
        <div className="relbig">
          <span data-testid="connector-active-release" className="r num">
            {maxVersion !== null ? `r${maxVersion}` : "—"}
          </span>
          <span className="l">
            {activeRelease ? `live since ${formatDayTime(activeRelease.created_at)}` : "no release yet"}
          </span>
        </div>
      </section>

      <div className="px-10 pt-4">
        <Link href="/connectors" data-testid="back-to-connectors" className="link">
          ← Back to connectors
        </Link>
      </div>

      {waitingRepair ? (
        <div className="wait">
          <p>
            <b>Repair {waitingRepair.id.slice(0, 8)} is waiting for you.</b>{" "}
            {waitingRepair.rule_id ?? openDrift[0]?.drift_class ?? "Drift"} at L
            {waitingRepair.ladder_level_reached} ·{" "}
            {frontierTokensOf(waitingRepair).toLocaleString()} frontier tokens.
          </p>
          <Link className="btn" href={`/repairs/${encodeURIComponent(waitingRepair.id)}`}>
            Review repair {waitingRepair.id.slice(0, 8)}
          </Link>
        </div>
      ) : openDrift.length ? (
        <div className="wait">
          <p>
            <b>{openDrift.length} open drift.</b> No repair proposed yet — trigger a sync to start the climb.
          </p>
        </div>
      ) : null}

      <div className="px-10 pt-4">
        <button
          data-testid="sync-now-button"
          onClick={handleSync}
          disabled={syncState === "sending"}
          className="btn quiet"
        >
          {syncState === "sending" ? "Starting sync…" : "Sync now"}
        </button>
        {syncMsg ? (
          <div
            data-testid={syncState === "error" ? "sync-error" : "sync-success"}
            className={`mt-3 border px-4 py-2 text-sm ${syncState === "error" ? "border-stop bg-band text-stop" : "border-rule bg-band text-ink"}`}
          >
            {syncMsg}
          </div>
        ) : null}
      </div>

      <section className="cols">
        <div>
          <h2 className="sec">Sync runs</h2>
          <SyncRunsTable syncRuns={syncRuns ?? (detail.last_sync ? [detail.last_sync as SyncRunOut] : [])} />
        </div>
        <div>
          <h2 className="sec">Releases</h2>
          <ReleaseHistory releases={detail.releases ?? []} activeReleaseId={detail.active_release_id} />
        </div>
      </section>

      <section className="spec" aria-labelledby="spec-t">
        <div>
          <h2 className="sec" id="spec-t">
            Spec{specRelease ? `, r${specRelease.version}` : ""}
          </h2>
          {detail.releases?.length ? (
            <select
              data-testid="release-selector"
              value={specRelease?.id ?? ""}
              onChange={(e) => setSelectedReleaseId(e.target.value || null)}
              className="mb-3 border border-rule bg-band px-2 py-1 text-sm"
              aria-label="Select release for spec viewer"
            >
              {(detail.releases ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  r{r.version} — {r.id.slice(0, 8)}{r.id === detail.active_release_id ? " (active)" : ""}
                </option>
              ))}
            </select>
          ) : null}
          <ul className="toc" aria-label="Spec sections">
            {specRelease?.spec && typeof specRelease.spec === "object"
              ? Object.keys(specRelease.spec as Record<string, unknown>)
                  .slice(0, 8)
                  .map((key) => (
                    <li key={key}>
                      <a href="#spec-t">{key}</a>
                    </li>
                  ))
              : null}
          </ul>
        </div>
        {specRelease ? (
          <SpecViewer spec={specRelease.spec as Record<string, unknown>} title={`Spec — r${specRelease.version} (${specRelease.id.slice(0, 8)})`} />
        ) : (
          <div data-testid="spec-viewer-empty" className="border border-rule px-4 py-6 text-sm text-ink-2">
            No releases yet. Build a connector to see its spec.
          </div>
        )}
      </section>

      <section className="foot">
        <div>
          <h2 className="sec">Fingerprint</h2>
          {(syncRuns ?? []).length > 0 || detail.last_sync?.fingerprint_id ? (
            <div data-testid="fingerprints-section">
              <dl className="fp num">
                {(syncRuns ?? (detail.last_sync ? [detail.last_sync] : [])).map(
                  (run: SyncRunOut | typeof detail.last_sync) =>
                    run?.fingerprint_id ? (
                      <div key={run.id} className="contents" data-testid={`fingerprint-entry-${run.id}`}>
                        <dt>{run.id.slice(0, 8)}</dt>
                        <dd>
                          {run.fingerprint_id} <span className="font-normal text-ink-2">({run.outcome})</span>
                        </dd>
                      </div>
                    ) : null,
                )}
              </dl>
            </div>
          ) : (
            <p className="text-sm text-ink-2">No fingerprints recorded yet.</p>
          )}
        </div>
        <div>
          <h2 className="sec">Dependencies</h2>
          <DependencyList dependencies={(detail.dependencies ?? []) as DependencyOut[]} />
        </div>
      </section>
    </div>
  );
}
