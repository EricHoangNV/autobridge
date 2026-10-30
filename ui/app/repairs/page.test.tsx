import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import RepairsPage, { currentRungOf, inFlightCallsOf, repairStatus } from "./page";
import type { RepairRunOut } from "@/lib/api/client";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

const REPAIRS = [
  {
    id: "rep-1",
    drift_event_ids: ["drift-1"],
    ladder_level_reached: 1,
    drift_class: "FIELD_RENAME",
    rule_id: "rename_field",
    hypothesis_ids: [],
    visible_test_results: [{ name: "records_match", passed: true }],
    verifier_report_id: "report-1",
    decision: "propose",
    model_usage: { frontier_tokens: 0, small_tokens: 0, calls: 0 },
    created_at: "2026-10-29T11:00:00Z",
    approval: null,
  },
  {
    id: "rep-2",
    drift_event_ids: ["drift-2"],
    ladder_level_reached: 4,
    drift_class: "SEMANTIC_VALUE_DRIFT",
    rule_id: null,
    hypothesis_ids: [],
    visible_test_results: [],
    verifier_report_id: null,
    decision: "escalate",
    model_usage: {},
    created_at: "2026-10-29T12:00:00Z",
    approval: null,
    escalation_bundle: { question: "Which mapping is correct?" },
  },
];

const DRIFTS = [
  { id: "drift-1", connector_id: "conn-atlas" },
  { id: "drift-2", connector_id: "conn-harbor" },
];

const CONNECTORS = [
  { id: "conn-atlas", name: "AtlasPMS", active_release_id: "rel-a2" },
  { id: "conn-harbor", name: "HarborPMS", active_release_id: "rel-h1" },
];

function mockAll(repairs: unknown = REPAIRS, drifts: unknown = DRIFTS, connectors: unknown = CONNECTORS) {
  mockedUseSWR
    .mockReturnValueOnce({ data: repairs, error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: drifts, error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: connectors, error: undefined, isLoading: false });
}

describe("RepairsPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows loading", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true } as unknown as ReturnType<typeof useSWR>);
    render(<RepairsPage />);
    expect(screen.getByText(/Loading repairs/)).toBeInTheDocument();
  });

  it("shows empty when no repairs", () => {
    mockAll([], [], []);
    render(<RepairsPage />);
    expect(screen.getByTestId("repairs-empty")).toBeInTheDocument();
  });

  it("lists newest first with connector, class, rung, rule, calls and status", () => {
    mockAll();
    render(<RepairsPage />);
    expect(screen.getByTestId("repair-row-rep-1")).toBeInTheDocument();
    expect(screen.getByTestId("repair-row-rep-2")).toBeInTheDocument();
    // Newest first: rep-2 (12:00) before rep-1 (11:00).
    const rows = screen.getAllByTestId(/^repair-row-/);
    expect(rows[0]).toHaveAttribute("data-testid", "repair-row-rep-2");

    expect(screen.getByTestId("repair-connector-rep-1")).toHaveTextContent("AtlasPMS");
    expect(screen.getByTestId("repair-connector-rep-2")).toHaveTextContent("HarborPMS");
    expect(screen.getByTestId("repair-class-rep-1")).toHaveTextContent("FIELD_RENAME");
    expect(screen.getByTestId("repair-ladder-rep-1")).toHaveTextContent("L1");
    expect(screen.getByTestId("repair-ladder-rep-2")).toHaveTextContent("L4");
    expect(screen.getByTestId("repair-rule-rep-1")).toHaveTextContent("rename_field");
    // Zero-token repair says "0 model calls" explicitly.
    expect(screen.getByTestId("repair-calls-rep-1")).toHaveTextContent("0 model calls");
    // Verified patch waiting → fix ready; escalation → needs your decision.
    expect(screen.getByTestId("repair-status-rep-1")).toHaveTextContent("fix ready");
    expect(screen.getByTestId("repair-status-rep-2")).toHaveTextContent("needs your decision");
  });

  it("shows approved/applied from the persisted approval row", () => {
    const approved = {
      ...REPAIRS[0],
      id: "rep-3",
      approval: { id: "ap-1", actor: "operator", decision: "approve", from_release_id: "rel-a1", to_release_id: "rel-a2", created_at: "2026-10-29T11:30:00Z" },
    };
    mockAll([approved], DRIFTS, CONNECTORS);
    render(<RepairsPage />);
    // rel-a2 is AtlasPMS's active release → applied.
    expect(screen.getByTestId("repair-status-rep-3")).toHaveTextContent("applied");
  });

  it("shows error", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: new Error("fail"), isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<RepairsPage />);
    expect(screen.getByTestId("repairs-error")).toBeInTheDocument();
  });
});

describe("repairStatus", () => {
  const drifts = new Map([["d1", "c1"]]);
  const base: RepairRunOut = {
    id: "r",
    drift_event_ids: ["d1"],
    ladder_level_reached: 1,
    vendor: null,
    drift_signature: null,
    drift_class: null,
    approval: null,
    fingerprint_delta: null,
    candidate_patch: null,
    rule_id: null,
    hypothesis_ids: [],
    visible_test_results: [],
    verifier_report_id: null,
    decision: "propose",
    model_usage: {},
    ladder_trace: [],
    escalation_bundle: null,
    created_at: "2026-10-29T11:00:00Z",
    kind: "repair",
  };

  it("maps rejected decisions and approvals", () => {
    expect(repairStatus({ ...base, decision: "reject" }, {}, drifts)).toBe("rejected");
    expect(
      repairStatus({ ...base, approval: { id: "a", actor: "op", decision: "reject", created_at: "t" } }, {}, drifts),
    ).toBe("rejected");
  });

  it("maps applied vs approved via the active release", () => {
    const approved = {
      ...base,
      approval: { id: "a", actor: "op", decision: "approve", to_release_id: "rel-9", created_at: "t" },
    };
    expect(repairStatus(approved, { c1: "rel-9" }, drifts)).toBe("applied");
    expect(repairStatus(approved, { c1: "rel-8" }, drifts)).toBe("approved");
  });

  it("maps escalations, failures and undecided repairs to needs your decision", () => {
    expect(repairStatus({ ...base, decision: "escalate" }, {}, drifts)).toBe("needs your decision");
    expect(repairStatus({ ...base, decision: "failed" }, {}, drifts)).toBe("needs your decision");
    expect(repairStatus({ ...base }, {}, drifts)).toBe("needs your decision");
  });

  it("maps running repairs to repairing", () => {
    expect(repairStatus({ ...base, decision: "climbing" }, {}, drifts)).toBe("repairing");
    // Even a climbing row that already carries a candidate still reads
    // as running until the climb writes its decision.
    expect(
      repairStatus({ ...base, decision: "climbing", candidate_patch: [{ op: "replace" }] }, {}, drifts),
    ).toBe("repairing");
  });

  it("maps verified patches to fix ready", () => {
    expect(repairStatus({ ...base, verifier_report_id: "vr" }, {}, drifts)).toBe("fix ready");
    expect(repairStatus({ ...base, candidate_patch: [{ op: "replace" }] }, {}, drifts)).toBe("fix ready");
  });

  it("keeps table cell text in sync", () => {
    mockAll();
    render(<RepairsPage />);
    const status = screen.getByTestId("repair-status-rep-1");
    expect(within(status).getByText("fix ready")).toBeInTheDocument();
  });
});

describe("currentRungOf", () => {
  const drifts = new Map([["d1", "c1"]]);
  const mk = (trace: Array<Record<string, unknown>>, level: number) => ({
    id: "r",
    drift_event_ids: ["d1"],
    ladder_level_reached: level,
    vendor: null,
    drift_signature: null,
    drift_class: null,
    approval: null,
    fingerprint_delta: null,
    candidate_patch: null,
    rule_id: null,
    hypothesis_ids: [],
    visible_test_results: [],
    verifier_report_id: null,
    decision: "climbing",
    model_usage: {},
    ladder_trace: trace,
    escalation_bundle: null,
    created_at: "2026-10-29T11:00:00Z",
    kind: "repair" as const,
  });

  it("reads the rung from the trace while climbing", () => {
    expect(currentRungOf(mk([{ level: 0 }, { level: 3 }], 0) as never)).toBe(3);
    expect(currentRungOf(mk([], 0) as never)).toBe(0);
    expect(currentRungOf(mk([{ level: 1 }], 4) as never)).toBe(4);
  });
});

describe("inFlightCallsOf (REQ-048)", () => {
  const base = {
    id: "r",
    drift_event_ids: ["d1"],
    ladder_level_reached: 2,
    decision: "climbing",
    model_usage: {},
    ladder_trace: [],
    created_at: "2026-10-29T11:00:00Z",
  };
  const stamp = { level: 3, action: "frontier_generate", reason: "L3 invoked", model: "muse-spark-1.3", call_started_at: "2026-10-29T11:00:04Z" };

  it("counts rung snapshots with a model call in flight", () => {
    expect(inFlightCallsOf({ ...base, ladder_trace: [stamp] } as never)).toBe(1);
    expect(inFlightCallsOf({ ...base, ladder_trace: [] } as never)).toBe(0);
    // A finished row never reads as in flight, even with a stale stamp.
    expect(inFlightCallsOf({ ...base, decision: "escalate", ladder_trace: [stamp] } as never)).toBe(0);
  });

  it("shows in-flight instead of zero calls while the call runs", () => {
    const running = { ...base, id: "rep-run", ladder_trace: [stamp] };
    mockAll([running], [{ id: "d1", connector_id: "conn-atlas" }], CONNECTORS);
    render(<RepairsPage />);
    expect(screen.getByTestId("repair-calls-rep-run")).toHaveTextContent("1 call in flight");
  });
});
