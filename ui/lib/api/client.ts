/**
 * Typed API client for AutoBridge control plane (P1.W7.3, plan §2.11).
 *
 * Generated types come from `ui/openapi.json` via openapi-typescript.
 * Never hand-write API types here — they are the contract.
 *
 * Usage:
 *   import { createApiClient } from "./client";
 *   const api = createApiClient("http://localhost:8101");
 *   const { data, error } = await api.GET("/connectors");
 */

import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema.d.ts";

// ---------------------------------------------------------------------------
// Re-export schema types (so UI screens import from one place)
// ---------------------------------------------------------------------------

export type ConnectorCreate = components["schemas"]["ConnectorCreate"];
export type ConnectorOut = components["schemas"]["ConnectorOut"];
export type ConnectorDetail = components["schemas"]["ConnectorDetail"];
export type BuildRequest = components["schemas"]["BuildRequest"];
export type BuildResponse = components["schemas"]["BuildResponse"];
export type SyncRequest = components["schemas"]["SyncRequest"];
export type SyncResponse = components["schemas"]["SyncResponse"];
export type DriftEventOut = components["schemas"]["DriftEventOut"];
export type RepairRunOut = components["schemas"]["RepairRunOut"];
export type ReleaseOut = components["schemas"]["ReleaseOut"];
export type ApprovalOut = components["schemas"]["ApprovalOut"];
export type RollbackRequest = components["schemas"]["RollbackRequest"];
export type RollbackResponse = components["schemas"]["RollbackResponse"];
export type RepairDecisionRequest = components["schemas"]["RepairDecisionRequest"];
export type InjectFaultRequest = components["schemas"]["InjectFaultRequest"];
export type InjectFaultResponse = components["schemas"]["InjectFaultResponse"];
export type ModelUsageResponse = components["schemas"]["ModelUsageResponse"];
export type ScorecardsResponse = components["schemas"]["ScorecardsResponse"];
export type JobOut = components["schemas"]["JobOut"];
export type EvidenceOut = components["schemas"]["EvidenceOut"];
export type SyncRunOut = components["schemas"]["SyncRunOut"];
export type RunOut = components["schemas"]["RunOut"];
export type DependencyCreate = components["schemas"]["DependencyCreate"];
export type DependencyOut = components["schemas"]["DependencyOut"];
export type OperatorOut = components["schemas"]["OperatorOut"];
export type DiscoveryRecordOut = components["schemas"]["DiscoveryRecordOut"];
export type DiscoveryImportResponse = components["schemas"]["DiscoveryImportResponse"];

// ---------------------------------------------------------------------------
// Client factory
// ---------------------------------------------------------------------------

export type ApiClient = ReturnType<typeof createClient<paths>>;

export function createApiClient(
  baseUrl?: string,
  opts?: { fetch?: typeof fetch; middleware?: Middleware },
): ApiClient {
  const url =
    baseUrl ??
    (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_URL : undefined) ??
    "http://localhost:8101";
  const client = createClient<paths>({ baseUrl: url, fetch: opts?.fetch });
  if (opts?.middleware) {
    client.use(opts.middleware);
  }
  return client;
}

// Default singleton for browser (baseUrl resolved at runtime via env or window).
export const api = createApiClient();

// ---------------------------------------------------------------------------
// Convenience wrappers — each keeps full type safety via paths lookup
// ---------------------------------------------------------------------------

export async function listConnectors(client: ApiClient = api) {
  return client.GET("/connectors");
}

export async function createConnector(
  body: ConnectorCreate,
  client: ApiClient = api,
) {
  return client.POST("/connectors", { body });
}

export async function getConnector(connectorId: string, client: ApiClient = api) {
  return client.GET("/connectors/{connector_id}", {
    params: { path: { connector_id: connectorId } },
  });
}

export async function listSyncRuns(connectorId: string, client: ApiClient = api) {
  return client.GET("/connectors/{connector_id}/sync-runs", {
    params: { path: { connector_id: connectorId } },
  });
}

export async function listDependencies(
  connectorId: string,
  client: ApiClient = api,
) {
  return client.GET("/connectors/{connector_id}/dependencies", {
    params: { path: { connector_id: connectorId } },
  });
}

export async function createDependency(
  connectorId: string,
  body: DependencyCreate,
  client: ApiClient = api,
) {
  return client.POST("/connectors/{connector_id}/dependencies", {
    params: { path: { connector_id: connectorId } },
    body,
  });
}

export async function triggerBuild(
  connectorId: string,
  body: BuildRequest,
  client: ApiClient = api,
) {
  return client.POST("/connectors/{connector_id}/build", {
    params: { path: { connector_id: connectorId } },
    body,
  });
}

export async function triggerSync(
  connectorId: string,
  body: SyncRequest,
  client: ApiClient = api,
) {
  return client.POST("/connectors/{connector_id}/sync", {
    params: { path: { connector_id: connectorId } },
    body,
  });
}

export async function getRun(runId: string, client: ApiClient = api) {
  return client.GET("/runs/{run_id}", {
    params: { path: { run_id: runId } },
  });
}

export async function getRunDetail(runId: string, client: ApiClient = api) {
  return client.GET("/runs/{run_id}/detail", {
    params: { path: { run_id: runId } },
  });
}

export async function getRunEvidence(runId: string, client: ApiClient = api) {
  return client.GET("/runs/{run_id}/evidence", {
    params: { path: { run_id: runId } },
  });
}

export async function getJob(jobId: string, client: ApiClient = api) {
  return client.GET("/jobs/{job_id}", {
    params: { path: { job_id: jobId } },
  });
}

export async function listJobs(client: ApiClient = api) {
  return client.GET("/jobs");
}

export async function getEvidence(evidenceId: string, client: ApiClient = api) {
  return client.GET("/evidence/{evidence_id}", {
    params: { path: { evidence_id: evidenceId } },
  });
}

export async function listDrift(
  params?: { connector_id?: string; status?: string },
  client: ApiClient = api,
) {
  return client.GET("/drift", {
    params: { query: params ?? {} },
  });
}

export async function getDrift(driftId: string, client: ApiClient = api) {
  return client.GET("/drift/{drift_id}", {
    params: { path: { drift_id: driftId } },
  });
}

export async function listRepairs(
  params?: { connector_id?: string },
  client: ApiClient = api,
) {
  return client.GET("/repairs", {
    params: { query: params ?? {} },
  });
}

export async function getRepair(repairId: string, client: ApiClient = api) {
  return client.GET("/repairs/{repair_id}", {
    params: { path: { repair_id: repairId } },
  });
}

export async function approveRepair(
  repairId: string,
  body: RepairDecisionRequest,
  client: ApiClient = api,
) {
  return client.POST("/repairs/{repair_id}/approve", {
    params: { path: { repair_id: repairId } },
    body,
  });
}

export async function rejectRepair(
  repairId: string,
  body: RepairDecisionRequest,
  client: ApiClient = api,
) {
  return client.POST("/repairs/{repair_id}/reject", {
    params: { path: { repair_id: repairId } },
    body,
  });
}

export async function listReleases(
  params?: { connector_id?: string },
  client: ApiClient = api,
) {
  return client.GET("/releases", {
    params: { query: params ?? {} },
  });
}

export async function getRelease(releaseId: string, client: ApiClient = api) {
  return client.GET("/releases/{release_id}", {
    params: { path: { release_id: releaseId } },
  });
}

export async function rollbackRelease(
  releaseId: string,
  body: RollbackRequest,
  client: ApiClient = api,
) {
  return client.POST("/releases/{release_id}/rollback", {
    params: { path: { release_id: releaseId } },
    body,
  });
}

export async function injectFault(
  body: InjectFaultRequest,
  client: ApiClient = api,
) {
  return client.POST("/demo/inject-fault", { body });
}

export async function resetDemo(client: ApiClient = api) {
  return client.POST("/demo/reset");
}

export async function getModelUsage(client: ApiClient = api) {
  return client.GET("/metrics/model-usage");
}

export async function getSyncOutcomes(client: ApiClient = api) {
  return client.GET("/metrics/sync-outcomes");
}

export async function listScorecards(client: ApiClient = api) {
  return client.GET("/bench/scorecards");
}

export async function getScorecard(
  params?: { path?: string | null },
  client: ApiClient = api,
) {
  return client.GET("/bench/scorecard", {
    params: { query: params ?? {} },
  });
}

export async function getOperator(client: ApiClient = api) {
  return client.GET("/operator");
}

export async function getHealthz(client: ApiClient = api) {
  return client.GET("/healthz");
}

export async function getReadyz(client: ApiClient = api) {
  return client.GET("/readyz");
}

/**
 * SSE helper — EventSource for run events (not via openapi-fetch).
 * The caller is responsible for closing the EventSource.
 */
export function subscribeRunEvents(
  runId: string,
  baseUrl?: string,
): EventSource {
  const base =
    baseUrl ??
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:8101");
  // Prefer control baseUrl when passed; otherwise use window origin with /api proxy.
  const url = `${base.replace(/\/$/, "")}/runs/${encodeURIComponent(runId)}/events`;
  return new EventSource(url);
}
