"use client";

import useSWR from "swr";
import { createApiClient, type ConnectorOut, type ConnectorDetail, type ModelUsageResponse } from "./client";
import { apiBaseUrl } from "./base";

export { apiBaseUrl };

// Browser calls go to same-origin /api (REQ-018); the rewrite + middleware
// forward to control and attach the token server-side.
function getBaseUrl(): string {
  return apiBaseUrl();
}

const fetcher = async <T>(path: string): Promise<T> => {
  const base = getBaseUrl();
  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

export function useConnectors() {
  return useSWR<ConnectorOut[]>("/connectors", fetcher, { refreshInterval: 5000, revalidateOnFocus: true });
}

export function useConnectorDetail(id: string | null) {
  return useSWR<ConnectorDetail>(id ? `/connectors/${encodeURIComponent(id)}` : null, fetcher, {
    refreshInterval: 5000,
  });
}

export function useModelUsage() {
  return useSWR<ModelUsageResponse>("/metrics/model-usage", fetcher, { refreshInterval: 10000 });
}

export function createClientForSWR() {
  return createApiClient(getBaseUrl());
}
