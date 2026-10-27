import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { EvidenceGraph } from "./EvidenceGraph";

const drifts = [
  { id: "drift-001", connector_id: "conn-1", drift_class: "PAGINATION_CURSOR", evidence_ids: ["ev-001", "ev-missing"] },
];

const records = [
  { id: "ev-001", kind: "hypothesis", parent_ids: ["ev-000"], run_id: "run-1" },
  { id: "ev-000", kind: "probe", parent_ids: [], run_id: "run-1" },
];

describe("EvidenceGraph", () => {
  it("renders empty state without drift events", () => {
    render(<EvidenceGraph driftEvents={[]} />);
    expect(screen.getByTestId("evidence-graph-empty")).toBeInTheDocument();
  });

  it("renders drift, evidence and external/missing stub nodes", () => {
    const { container } = render(<EvidenceGraph driftEvents={drifts} evidenceRecords={records} />);
    expect(screen.getByTestId("evidence-graph")).toBeInTheDocument();
    // drift-001 + ev-001 + ev-000 + ev-missing stub = 4 nodes
    expect(screen.getByTestId("evidence-graph-node-drift-001")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-graph-node-ev-001")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-graph-node-ev-000")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-graph-node-ev-missing")).toBeInTheDocument();
    // edges: drift->ev-001, drift->ev-missing, ev-000->ev-001 = 3
    expect(container.querySelectorAll('[data-testid="evidence-graph-edge"]')).toHaveLength(3);
  });

  it("marks node kinds for styling", () => {
    render(<EvidenceGraph driftEvents={drifts} evidenceRecords={records} />);
    expect(screen.getByTestId("evidence-graph-node-drift-001").getAttribute("data-node-kind")).toBe("drift");
    expect(screen.getByTestId("evidence-graph-node-ev-missing").getAttribute("data-node-kind")).toBe("external");
  });

  it("caps nodes at maxNodes and reports the remainder", () => {
    render(<EvidenceGraph driftEvents={drifts} evidenceRecords={records} maxNodes={2} />);
    expect(screen.getByTestId("evidence-graph-more")).toHaveTextContent("Showing 2 of 4 nodes");
  });

  it("renders loading state", () => {
    render(<EvidenceGraph driftEvents={[]} loading />);
    expect(screen.getByTestId("evidence-graph-loading")).toBeInTheDocument();
  });

  it("handles parent cycles without hanging", () => {
    const cyclic = [
      { id: "a", kind: "probe", parent_ids: ["b"], run_id: "r" },
      { id: "b", kind: "probe", parent_ids: ["a"], run_id: "r" },
    ];
    const { container } = render(
      <EvidenceGraph driftEvents={[{ id: "d", connector_id: "c", drift_class: "X", evidence_ids: ["a"] }]} evidenceRecords={cyclic} />,
    );
    expect(screen.getByTestId("evidence-graph")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-testid="evidence-graph-edge"]').length).toBeGreaterThan(0);
  });
});
