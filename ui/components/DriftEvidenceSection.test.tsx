import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DriftEvidenceSection } from "./DriftEvidenceSection";

describe("DriftEvidenceSection", () => {
  it("shows empty when no drift events", () => {
    render(<DriftEvidenceSection driftEvents={[]} />);
    expect(screen.getByTestId("drift-evidence-empty")).toBeInTheDocument();
  });

  it("renders drift event with class and severity", () => {
    render(
      <DriftEvidenceSection
        driftEvents={[
          {
            id: "drift-1",
            connector_id: "c1",
            drift_class: "FIELD_RENAMED",
            severity: "high",
            evidence_ids: ["ev-1"],
            source_sync_run_id: "sync-1",
            status: "open",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ]}
      />,
    );
    expect(screen.getByTestId("drift-evidence-section")).toBeInTheDocument();
    expect(screen.getByTestId("drift-event-drift-1")).toBeInTheDocument();
    expect(screen.getByTestId("drift-class-drift-1")).toHaveTextContent("FIELD_RENAMED");
    expect(screen.getByTestId("drift-severity-drift-1")).toHaveTextContent("high");
  });

  it("shows loading state", () => {
    render(<DriftEvidenceSection driftEvents={[]} loading={true} />);
    expect(screen.getByTestId("drift-evidence-loading")).toBeInTheDocument();
  });
});
