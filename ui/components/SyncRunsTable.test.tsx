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

describe("SyncRunsTable edge link note (REQ-071)", () => {
  const edgeLink = (overrides: Record<string, unknown> = {}) => ({
    link_up: true,
    reconnects: 2,
    posting_keys_total: 2,
    posting_keys_unique: 2,
    posting_keys: ["k-a", "k-b"],
    posting_keys_truncated: false,
    ...overrides,
  });

  const edgeRun = (id: string, link: Record<string, unknown>, records: number) =>
    mkSyncRun({
      id,
      records_observed: { FolioCharge: records },
      data_readiness: { freshness: "current", completeness: "complete", edge_link: link },
    });

  it("notes a drop on the later row when the reconnect counter went up", () => {
    const earlier = edgeRun("s-earlier", edgeLink({ reconnects: 2, posting_keys: ["k-a", "k-b"] }), 10);
    const later = edgeRun(
      "s-later",
      edgeLink({ reconnects: 3, posting_keys_total: 3, posting_keys_unique: 3, posting_keys: ["k-a", "k-b", "k-c"] }),
      13,
    );
    // Newest first, the API order.
    render(<SyncRunsTable syncRuns={[later, earlier]} isEdge />);
    expect(screen.getByTestId("link-note-s-later")).toHaveTextContent(
      "link dropped, re-handshaked · +3 postings, 0 lost, 0 duplicated",
    );
    expect(screen.queryByTestId("link-note-s-earlier")).not.toBeInTheDocument();
  });

  it("reads link down when the link was down at sync time", () => {
    const run = edgeRun("s-down", edgeLink({ link_up: false, reconnects: 0 }), 10);
    render(<SyncRunsTable syncRuns={[run]} isEdge />);
    expect(screen.getByTestId("link-note-s-down")).toHaveTextContent("link down");
  });

  it("shows no note when nothing changed", () => {
    const earlier = edgeRun("s-earlier", edgeLink({ reconnects: 2 }), 10);
    const later = edgeRun("s-later", edgeLink({ reconnects: 2 }), 10);
    render(<SyncRunsTable syncRuns={[later, earlier]} isEdge />);
    expect(screen.queryByTestId("link-note-s-later")).not.toBeInTheDocument();
    expect(screen.queryByTestId("link-note-s-earlier")).not.toBeInTheDocument();
  });

  it("computes lost and duplicated from the relay idempotency keys", () => {
    const earlier = edgeRun("s-earlier", edgeLink({ reconnects: 4, posting_keys: ["k-a", "k-b", "k-c"] }), 10);
    const later = edgeRun(
      "s-later",
      edgeLink({
        reconnects: 5,
        posting_keys_total: 4,
        posting_keys_unique: 3,
        posting_keys: ["k-b", "k-c", "k-d"],
      }),
      12,
    );
    render(<SyncRunsTable syncRuns={[later, earlier]} isEdge />);
    expect(screen.getByTestId("link-note-s-later")).toHaveTextContent(
      "link dropped, re-handshaked · +2 postings, 1 lost, 1 duplicated",
    );
  });

  it("leaves non-edge rows unchanged", () => {
    const earlier = edgeRun("s-earlier", edgeLink({ reconnects: 2 }), 10);
    const later = edgeRun("s-later", edgeLink({ reconnects: 3 }), 13);
    render(<SyncRunsTable syncRuns={[later, earlier]} />);
    expect(screen.queryByTestId("link-note-s-later")).not.toBeInTheDocument();
    expect(screen.getByTestId("sync-run-row-s-later")).toBeInTheDocument();
  });
});
