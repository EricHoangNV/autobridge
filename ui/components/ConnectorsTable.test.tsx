import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectorsTable, waitingRepairs } from "./ConnectorsTable";
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
  state: "working",
  activity: "in sync · Complete 3 s ago",
  last_sync_at: new Date().toISOString(),
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
  state: "working",
  activity: "in sync · Complete 3 s ago",
  last_sync_at: new Date().toISOString(),
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
      total_reasoning_tokens: 0,
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
    // No repairs: no chart, just the honest empty line.
    expect(screen.queryByTestId("sparkline")).not.toBeInTheDocument();
  });

  it("draws bars only for two or more repairs (REQ-048)", () => {
    const c = baseConnector();
    const detail = detailWith();
    const one = {
      id: "repair-one",
      drift_event_ids: [],
      ladder_level_reached: 4,
      vendor: "atlas",
      drift_signature: null,
      drift_class: "SEMANTIC_VALUE_DRIFT",
      approval: null,
      fingerprint_delta: null,
      candidate_patch: null,
      rule_id: null,
      hypothesis_ids: [],
      visible_test_results: [],
      verifier_report_id: null,
      decision: "escalate",
      model_usage: { frontier_tokens: 3530, calls: 1 },
      ladder_trace: [],
      escalation_bundle: null,
      created_at: new Date().toISOString(),
      kind: "repair",
    };
    const { rerender } = render(
      <ConnectorsTable connectors={[c]} detailsById={{ [c.id]: detail }} repairsByConnector={{ [c.id]: [one as never] }} />,
    );
    // One repair: the number only, no chart.
    expect(screen.queryByTestId("sparkline")).not.toBeInTheDocument();
    expect(screen.getByTestId(`connector-tokens-${c.id}`)).toHaveTextContent("3,530");
    expect(screen.getByTestId(`connector-tokens-${c.id}`)).toHaveTextContent("in 1 repair");
    const two = { ...one, id: "repair-two", model_usage: { frontier_tokens: 1000, calls: 1 } };
    rerender(
      <ConnectorsTable connectors={[c]} detailsById={{ [c.id]: detail }} repairsByConnector={{ [c.id]: [one as never, two as never] }} />,
    );
    expect(screen.getByTestId("sparkline")).toBeInTheDocument();
    expect(screen.getByTestId(`connector-tokens-${c.id}`)).toHaveTextContent("in 2 repairs");
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

  it("links a running repair from the connector row", () => {
    const c = baseConnector({ state: "degrading", activity: "drift detected 4 s ago · repairing at L3" });
    const climbing = {
      id: "repair-running-1",
      drift_event_ids: ["drift-1"],
      ladder_level_reached: 0,
      vendor: "harbor",
      drift_signature: null,
      drift_class: "SEMANTIC_VALUE_DRIFT",
      approval: null,
      fingerprint_delta: null,
      candidate_patch: null,
      rule_id: null,
      hypothesis_ids: [],
      visible_test_results: [],
      verifier_report_id: null,
      decision: "climbing",
      model_usage: {},
      ladder_trace: [
        { level: 0, action: "knowledge_miss", reason: "L0 miss" },
        { level: 1, action: "no_rule_matched", reason: "L1 miss" },
        { level: 2, action: "l2_skipped", reason: "L2 skipped" },
        { level: 3, action: "frontier_generate", reason: "L3 invoked", model: "sleep-frontier" },
      ],
      escalation_bundle: null,
      created_at: new Date().toISOString(),
      kind: "repair",
    };
    render(<ConnectorsTable connectors={[c]} repairsByConnector={{ [c.id]: [climbing as never] }} />);
    const link = screen.getByTestId(`connector-repair-link-${c.id}`);
    expect(link).toHaveTextContent("repairing at L3");
    expect(link.getAttribute("href")).toBe("/repairs/repair-running-1");
  });

  it("shows no repair link without a running repair", () => {
    const c = baseConnector();
    render(<ConnectorsTable connectors={[c]} repairsByConnector={{}} />);
    expect(screen.queryByTestId(`connector-repair-link-${c.id}`)).not.toBeInTheDocument();
  });

  it("approved repairs never wait; rollbacks wait again (REQ-053 item 3)", () => {
    const waiting = {
      id: "repair-wait",
      drift_event_ids: ["drift-1"],
      ladder_level_reached: 1,
      vendor: "atlas",
      drift_signature: null,
      drift_class: "PAGINATION_CHANGE",
      approval: null,
      fingerprint_delta: null,
      candidate_patch: [{ op: "replace" }],
      rule_id: "page_to_cursor",
      hypothesis_ids: [],
      visible_test_results: [],
      verifier_report_id: null,
      decision: "propose",
      model_usage: {},
      ladder_trace: [],
      escalation_bundle: null,
      created_at: new Date().toISOString(),
      kind: "repair",
    };
    expect(waitingRepairs([waiting as never])).toHaveLength(1);
    const approved = {
      ...waiting,
      id: "repair-done",
      decision: "approved",
      approval: { id: "a", actor: "op", decision: "approve", created_at: new Date().toISOString() },
    };
    expect(waitingRepairs([approved as never])).toHaveLength(0);
    const reopened = {
      ...waiting,
      id: "repair-re",
      approval: { id: "b", actor: "engine", decision: "rollback", created_at: new Date().toISOString() },
    };
    expect(waitingRepairs([reopened as never])).toHaveLength(1);
  });

  it("a superseded former active release is not a waiting candidate (REQ-053 item 3)", () => {
    const c = baseConnector({ active_release_id: "rel-2" });
    const detail = detailWith({
      active_release_id: "rel-2",
      releases: [
        { id: "rel-1", connector_id: c.id, version: 1, spec: {}, parent_release_id: null, provenance_build_run_id: "b", provenance_repair_run_id: null, verification_report_id: null, signature: "s1", created_at: new Date().toISOString() },
        { id: "rel-2", connector_id: c.id, version: 2, spec: {}, parent_release_id: "rel-1", provenance_build_run_id: null, provenance_repair_run_id: "repair-done", verification_report_id: null, signature: "s2", created_at: new Date().toISOString() },
      ] as unknown as ConnectorDetail["releases"],
    });
    const done = {
      id: "repair-done",
      drift_event_ids: ["drift-1"],
      ladder_level_reached: 1,
      vendor: "atlas",
      drift_signature: null,
      drift_class: "PAGINATION_CHANGE",
      approval: { id: "a", actor: "op", decision: "approve", created_at: new Date().toISOString() },
      fingerprint_delta: null,
      candidate_patch: [{ op: "replace" }],
      rule_id: "page_to_cursor",
      hypothesis_ids: [],
      visible_test_results: [],
      verifier_report_id: null,
      decision: "approved",
      model_usage: {},
      ladder_trace: [],
      escalation_bundle: null,
      created_at: new Date().toISOString(),
      kind: "repair",
    };
    render(<ConnectorsTable connectors={[c]} detailsById={{ [c.id]: detail }} repairsByConnector={{ [c.id]: [done as never] }} />);
    // r2 is active; r1 is superseded history — no "r1 waiting".
    expect(screen.queryByText(/waiting/)).not.toBeInTheDocument();
  });
});
