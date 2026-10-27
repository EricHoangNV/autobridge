"use client";

import useSWR from "swr";
import { createApiClient, type ConnectorOut, type ConnectorDetail, type ModelUsageResponse } from "./client";

// Resolve API base: browser uses NEXT_PUBLIC_API_URL or window origin proxy; server uses env.
function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    // In Compose the UI talks to control over engine-net via /api proxy; locally NEXT_PUBLIC_API_URL.
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
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

export function apiBaseUrl(): string {
  return getBaseUrl();
}

export function createClientForSWR() {
  return createApiClient(getBaseUrl());
}
