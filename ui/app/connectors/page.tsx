"use client";

import useSWR from "swr";
import { ConnectorsTable } from "@/components/ConnectorsTable";
import type { ConnectorOut, ConnectorDetail, ModelUsageResponse } from "@/lib/api/client";

// Reuse the same fetcher pattern as hooks: base resolved per env.
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

export default function ConnectorsPage() {
  const { data: connectors, error: connError, isLoading: connLoading } = useSWR<ConnectorOut[]>(
    "/connectors",
    fetcher,
    { refreshInterval: 5000, revalidateOnFocus: true }
  );

  const { data: modelUsage } = useSWR<ModelUsageResponse>("/metrics/model-usage", fetcher, {
    refreshInterval: 10000,
  });

  // Fetch details for each connector to populate active release / last sync / drift.
  // In Compose this is N+1 but the list is tiny (plan: two simulators).
  const detailKeys = (connectors ?? []).map((c) => `/connectors/${encodeURIComponent(c.id)}`);
  // We fetch details via a single combined key to keep SWR simple.
  const { data: detailsMap } = useSWR<Record<string, ConnectorDetail>>(
    connectors?.length ? `details:${detailKeys.join(",")}` : null,
    async () => {
      const entries = await Promise.all(
        (connectors ?? []).map(async (c) => {
          try {
            const d = await fetcher<ConnectorDetail>(`/connectors/${encodeURIComponent(c.id)}`);
            return [c.id, d] as const;
          } catch {
            return [c.id, null] as const;
          }
        })
      );
      return Object.fromEntries(entries) as Record<string, ConnectorDetail>;
    },
    { refreshInterval: 5000 }
  );

  return (
    <div data-testid="connectors-page">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Connectors</h1>
          <p className="mt-1 text-sm text-zinc-500">Health, active release, last sync outcome, open drift, and model usage.</p>
        </div>
        <a
          href="/connectors/new"
          data-testid="new-connector-link"
          className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800"
        >
          New connector
        </a>
      </div>

      {connLoading ? (
        <p data-testid="connectors-loading" className="py-8 text-center text-sm text-zinc-500">Loading connectors…</p>
      ) : connError ? (
        <div
          data-testid="connectors-error"
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          Failed to load connectors: {(connError as Error).message}
        </div>
      ) : (
        <ConnectorsTable connectors={connectors ?? []} detailsById={detailsMap ?? {}} modelUsage={modelUsage ?? null} />
      )}

      {connectors?.length ? (
        <p data-testid="connectors-count" className="mt-3 text-xs text-zinc-400">
          {connectors.length} connector{connectors.length === 1 ? "" : "s"} · polling every 5s
        </p>
      ) : null}
    </div>
  );
}
