import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SyncRunsTable } from "./SyncRunsTable";
import type { SyncRunOut } from "@/lib/api/client";

const mkSyncRun = (overrides: Partial<SyncRunOut> = {}): SyncRunOut =>
  ({
    id: `sync-${Math.random().toString(36).slice(2, 8)}`,
    release_id: "rel-1",
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    records_observed: { reservation: 10 },
    records_emitted: { reservation: 10 },
    checkpoint: {},
    invariant_results: [],
    outcome: "Complete",
    unmet_criteria: [],
    data_readiness: {},
    fingerprint_id: "fp-abc-123",
    kind: "sync",
    ...overrides,
  }) as SyncRunOut;

describe("SyncRunsTable", () => {
  it("renders empty state", () => {
    render(<SyncRunsTable syncRuns={[]} />);
    expect(screen.getByTestId("sync-runs-empty")).toBeInTheDocument();
  });

  it("renders Complete / Degraded / Blocked outcomes", () => {
    const runs = [
      mkSyncRun({ id: "s1", outcome: "Complete", unmet_criteria: [] }),
      mkSyncRun({ id: "s2", outcome: "Degraded", unmet_criteria: ["pagination_incomplete"] }),
      mkSyncRun({ id: "s3", outcome: "Blocked", unmet_criteria: ["auth_failed"] }),
    ];
    render(<SyncRunsTable syncRuns={runs} />);
    expect(screen.getByTestId("sync-runs-table")).toBeInTheDocument();
    expect(screen.getAllByTestId("sync-outcome-badge")).toHaveLength(3);
    expect(screen.getByTestId("sync-run-row-s1")).toBeInTheDocument();
    expect(screen.getByTestId("sync-run-row-s3")).toBeInTheDocument();
  });

  it("shows fingerprint and unmet criteria", () => {
    const run = mkSyncRun({ id: "s1", fingerprint_id: "fp-xyz-789", unmet_criteria: ["money_mismatch"] });
    render(<SyncRunsTable syncRuns={[run]} />);
    expect(screen.getByTestId("fingerprint-s1")).toBeInTheDocument();
    expect(screen.getByTestId("unmet-criteria-s1")).toHaveTextContent("money_mismatch");
  });

  it("renders dash when fingerprint missing", () => {
    // Should render dash or no fingerprint element
    const run = mkSyncRun({ id: "s1", fingerprint_id: null });
    render(<SyncRunsTable syncRuns={[run]} />);
    expect(screen.queryByTestId("fingerprint-s1")).not.toBeInTheDocument();
  });

  it("labels the fingerprint column and shows full values on hover", () => {
    const run = mkSyncRun({ id: "sync-full-id-1", fingerprint_id: "fp-xyz-789-full" });
    render(<SyncRunsTable syncRuns={[run]} />);
    expect(screen.getByText("Fingerprint").getAttribute("title")).toContain("a change means drift");
    expect(screen.getByTestId("fingerprint-sync-full-id-1").getAttribute("title")).toBe("fp-xyz-789-full");
  });
});
