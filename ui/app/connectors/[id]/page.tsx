"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { SpecViewer } from "@/components/SpecViewer";
import { ReleaseHistory } from "@/components/ReleaseHistory";
import { SyncRunsTable } from "@/components/SyncRunsTable";
import { DependencyList } from "@/components/DependencyList";
import { SyncOutcomeBadge } from "@/components/SyncOutcomeBadge";
import { HealthBadge } from "@/components/HealthBadge";
import type { ConnectorDetail, ReleaseOut, SyncRunOut, DependencyOut } from "@/lib/api/client";

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
      <div data-testid="connector-detail-page" className="py-8 text-center text-sm text-zinc-500">
        Loading connector…
      </div>
    );
  }

  if (detailError) {
    return (
      <div data-testid="connector-detail-page">
        <a href="/connectors" data-testid="back-to-connectors" className="text-xs text-zinc-500 hover:text-zinc-900 hover:underline">
          ← Back to connectors
        </a>
        <div
          data-testid="connector-detail-error"
          className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          Failed to load connector {(detailError as Error).message}
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div data-testid="connector-detail-page" className="py-8 text-center text-sm text-zinc-500">
        Connector not found.
      </div>
    );
  }

  return (
    <div data-testid="connector-detail-page">
      {/* Header */}
      <div className="mb-6">
        <a
          href="/connectors"
          data-testid="back-to-connectors"
          className="text-xs text-zinc-500 hover:text-zinc-900 hover:underline"
        >
          ← Back to connectors
        </a>
        <div className="mt-2 flex items-start justify-between gap-4">
          <div>
            <h1 data-testid="connector-name" className="text-xl font-semibold tracking-tight">
              {detail.name}
            </h1>
            <p className="mt-1 text-sm text-zinc-500">{detail.base_url}</p>
            <div className="mt-2 flex items-center gap-2">
              <HealthBadge status={detail.health_status} />
              {detail.last_sync ? <SyncOutcomeBadge outcome={detail.last_sync.outcome} /> : null}
              <span className="text-xs text-zinc-400">{detail.id.slice(0, 8)}</span>
            </div>
          </div>
          <button
            data-testid="trigger-sync-btn"
            onClick={handleSync}
            disabled={syncState === "sending"}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
          >
            {syncState === "sending" ? "Starting sync…" : "Sync now"}
          </button>
        </div>
        {syncMsg ? (
          <div
            data-testid={syncState === "error" ? "sync-error" : "sync-success"}
            className={`mt-3 rounded-lg border px-4 py-2 text-sm ${syncState === "error" ? "border-red-200 bg-red-50 text-red-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}
          >
            {syncMsg}
          </div>
        ) : null}
      </div>

      {/* Connector meta */}
      <section className="mb-8 rounded-lg border bg-zinc-50 px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Connector info</h2>
        <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div>
            <dt className="text-xs text-zinc-500">Credential ref</dt>
            <dd data-testid="connector-credential-ref" className="font-mono text-xs">
              {detail.credential_ref}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Target entities</dt>
            <dd data-testid="connector-target-entities" className="text-xs">
              {(detail.target_entities ?? []).join(", ") || "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Vendor docs ref</dt>
            <dd className="text-xs text-zinc-600">{detail.vendor_docs_ref ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Runtime target</dt>
            <dd className="font-mono text-xs">{JSON.stringify(detail.runtime_target)}</dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Active release</dt>
            <dd data-testid="connector-active-release" className="font-mono text-xs">
              {detail.active_release_id ? detail.active_release_id.slice(0, 8) : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Open drift</dt>
            <dd data-testid="connector-open-drift-count" className="text-xs">
              {(detail.open_drift ?? []).length} open
            </dd>
          </div>
        </dl>
      </section>

      {/* Spec viewer */}
      <section className="mb-8">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Spec viewer</h2>
          {detail.releases?.length ? (
            <select
              data-testid="release-selector"
              value={specRelease?.id ?? ""}
              onChange={(e) => setSelectedReleaseId(e.target.value || null)}
              className="rounded border border-zinc-300 px-2 py-1 text-xs"
              aria-label="Select release for spec viewer"
            >
              {(detail.releases ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  v{r.version} — {r.id.slice(0, 8)}{r.id === detail.active_release_id ? " (active)" : ""}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        {specRelease ? (
          <SpecViewer spec={specRelease.spec as Record<string, unknown>} title={`Spec — v${specRelease.version} (${specRelease.id.slice(0, 8)})`} />
        ) : (
          <div data-testid="spec-viewer-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
            No releases yet. Build a connector to see its spec.
          </div>
        )}
      </section>

      {/* Release history */}
      <section className="mb-8">
        <h2 className="mb-2 text-sm font-semibold">Release history</h2>
        <ReleaseHistory releases={detail.releases ?? []} activeReleaseId={detail.active_release_id} />
      </section>

      {/* Sync runs */}
      <section className="mb-8">
        <h2 className="mb-2 text-sm font-semibold">Sync runs</h2>
        <SyncRunsTable syncRuns={syncRuns ?? (detail.last_sync ? [detail.last_sync as SyncRunOut] : [])} />
      </section>

      {/* Dependency registrations */}
      <section className="mb-8">
        <h2 className="mb-2 text-sm font-semibold">Dependency registrations</h2>
        <DependencyList dependencies={(detail.dependencies ?? []) as DependencyOut[]} />
      </section>

      {/* Fingerprints summary (from sync runs) */}
      {(syncRuns ?? []).length > 0 || detail.last_sync?.fingerprint_id ? (
        <section className="mb-8">
          <h2 className="mb-2 text-sm font-semibold">Fingerprints</h2>
          <div data-testid="fingerprints-section" className="rounded-lg border bg-white px-4 py-3">
            {(syncRuns ?? (detail.last_sync ? [detail.last_sync] : [])).length ? (
              <ul className="space-y-1 text-xs">
                {(syncRuns ?? (detail.last_sync ? [detail.last_sync] : [])).map((run: SyncRunOut | typeof detail.last_sync) =>
                  run?.fingerprint_id ? (
                    <li key={run.id} data-testid={`fingerprint-entry-${run.id}`} className="font-mono text-zinc-700">
                      {run.id.slice(0, 8)} → {run.fingerprint_id}
                      <span className="ml-2 text-zinc-500">({run.outcome})</span>
                    </li>
                  ) : null,
                )}
                {!(syncRuns ?? (detail.last_sync ? [detail.last_sync] : [])).some((r: SyncRunOut | typeof detail.last_sync) => r?.fingerprint_id) ? (
                  <li className="text-zinc-500">No fingerprints recorded yet.</li>
                ) : null}
              </ul>
            ) : (
              <p className="text-xs text-zinc-500">No fingerprints recorded yet.</p>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}
