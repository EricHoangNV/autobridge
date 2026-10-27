import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectorsTable } from "./ConnectorsTable";
import type { ConnectorOut, ConnectorDetail, ModelUsageResponse } from "@/lib/api/client";

const baseConnector = (overrides: Partial<ConnectorOut> = {}): ConnectorOut => ({
  id: "conn-001-abcdef",
  name: "Atlas PMS",
  vendor_docs_ref: null,
  base_url: "http://sim-atlas:8000",
  credential_ref: "ATLAS_API_KEY",
  target_entities: ["reservation"],
  runtime_target: {},
  active_release_id: "rel-1",
  health_status: "healthy",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...overrides,
});

const detailWith = (overrides: Partial<ConnectorDetail> = {}): ConnectorDetail => ({
  id: "conn-001-abcdef",
  name: "Atlas PMS",
  vendor_docs_ref: null,
  base_url: "http://sim-atlas:8000",
  credential_ref: "ATLAS_API_KEY",
  target_entities: ["reservation"],
  runtime_target: {},
  active_release_id: "rel-1",
  health_status: "healthy",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  releases: [{ id: "rel-1", connector_id: "conn-001-abcdef", version: 3, spec: {}, parent_release_id: null, provenance_build_run_id: null, provenance_repair_run_id: null, verification_report_id: null, signature: "sig", created_at: new Date().toISOString() }] as unknown as ConnectorDetail["releases"],
  last_sync: { id: "sync-1", release_id: "rel-1", started_at: new Date().toISOString(), finished_at: new Date().toISOString(), records_observed: {}, records_emitted: {}, checkpoint: {}, invariant_results: [], outcome: "Complete", unmet_criteria: [], data_readiness: {}, fingerprint_id: null, kind: "sync" } as unknown as ConnectorDetail["last_sync"],
  open_drift: [],
  dependencies: [],
  ...overrides,
});

describe("ConnectorsTable", () => {
  it("renders empty state", () => {
    render(<ConnectorsTable connectors={[]} />);
    expect(screen.getByTestId("connectors-empty")).toBeInTheDocument();
  });

  it("renders connector rows with health, release, sync outcome, drift, sparkline", () => {
    const c = baseConnector();
    const detail = detailWith();
    const modelUsage: ModelUsageResponse = {
      total_calls: 6,
      total_input_tokens: 100,
      total_output_tokens: 50,
      total_cost: "0.01",
      frontier_tokens: 0,
      small_tokens: 150,
      by_tier: {},
      by_provider: {},
      entries: [],
      by_purpose: {},
    };
    render(<ConnectorsTable connectors={[c]} detailsById={{ [c.id]: detail }} modelUsage={modelUsage} />);

    expect(screen.getByTestId("connectors-table")).toBeInTheDocument();
    expect(screen.getByTestId(`connector-row-${c.id}`)).toBeInTheDocument();
    expect(screen.getByText("Atlas PMS")).toBeInTheDocument();
    expect(screen.getByTestId("health-badge")).toHaveTextContent("healthy");
    expect(screen.getByText("r3")).toBeInTheDocument();
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("Complete");
    expect(screen.getByTestId(`drift-count-${c.id}`)).toHaveTextContent("0");
    expect(screen.getByTestId("sparkline")).toBeInTheDocument();
  });

  it("shows open drift count when drift exists", () => {
    const c = baseConnector();
    const detail = detailWith({
      open_drift: [{ id: "drift-1", connector_id: c.id, drift_class: "field_renamed", severity: "medium", evidence_ids: [], source_sync_run_id: null, status: "open", created_at: new Date().toISOString(), updated_at: new Date().toISOString() }] as unknown as ConnectorDetail["open_drift"],
    });
    render(<ConnectorsTable connectors={[c]} detailsById={{ [c.id]: detail }} />);
    expect(screen.getByTestId(`drift-count-${c.id}`)).toHaveTextContent("1 open");
  });

  it("shows Degraded and Blocked badges", () => {
    const c1 = baseConnector({ id: "c2", name: "Harbor", health_status: "healthy" });
    const d1 = detailWith({ id: "c2", last_sync: { outcome: "Degraded", unmet_criteria: ["pagination_incomplete"] } as unknown as ConnectorDetail["last_sync"] });
    const { rerender } = render(<ConnectorsTable connectors={[c1]} detailsById={{ c2: d1 }} />);
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("Degraded");

    const c2 = baseConnector({ id: "c3", name: "Harbor 2" });
    const d2 = detailWith({ id: "c3", last_sync: { outcome: "Blocked", unmet_criteria: ["auth_failed"] } as unknown as ConnectorDetail["last_sync"] });
    rerender(<ConnectorsTable connectors={[c2]} detailsById={{ "c3": d2 }} />);
    // second render replaces; check still works
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("Blocked");
  });

  it("renders two connectors", () => {
    const c1 = baseConnector({ id: "c-a", name: "Atlas" });
    const c2 = baseConnector({ id: "c-b", name: "Harbor", health_status: "degraded", base_url: "http://sim-harbor:8000" });
    render(<ConnectorsTable connectors={[c1, c2]} />);
    expect(screen.getByText("Atlas")).toBeInTheDocument();
    expect(screen.getByText("Harbor")).toBeInTheDocument();
    expect(screen.getAllByTestId("health-badge")).toHaveLength(2);
  });
});
