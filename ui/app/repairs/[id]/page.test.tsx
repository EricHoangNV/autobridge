import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import RepairDetailPage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

const mockRepair = {
  id: "repair-abc-123",
  drift_event_ids: ["drift-1"],
  ladder_level_reached: 1,
  vendor: "atlaspms",
  drift_class: "PAGINATION_CHANGED",
  fingerprint_delta: {
    pagination_changed: true,
    pagination_details: { list_reservations: { previous: { strategy: "page" }, current: { strategy: "cursor" } } },
  },
  candidate_patch: [{ op: "replace", path: "/operations/list/pagination/strategy", value: "cursor" }],
  rule_id: "page_to_cursor",
  hypothesis_ids: ["hyp-1"],
  visible_test_results: [{ name: "records_match", passed: true }],
  verifier_report_id: "report-xyz",
  decision: "propose",
  model_usage: { frontier_tokens: 0, small_tokens: 0, calls: 0 },
  ladder_trace: [
    { level: 0, action: "knowledge lookup", reason: "L0 miss: no known fix for atlaspms + PAGINATION_CHANGED" },
    { level: 1, action: "rule page_to_cursor matched", reason: "pagination shape page → cursor on the wire" },
  ],
  escalation_bundle: null,
  approval: null,
  created_at: new Date().toISOString(),
};

const mockDrift = {
  id: "drift-1",
  connector_id: "c1",
  drift_class: "PAGINATION_CHANGED",
  severity: "high",
  evidence_ids: ["ev-1"],
  source_sync_run_id: "sync-1",
  status: "open",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const mockEvidence = {
  id: "ev-1",
  kind: "observation",
  payload: { url: "GET /reservations", note: "page param ignored" },
  parent_ids: [],
  run_id: "sync-1",
  created_at: new Date().toISOString(),
};

const mockDerivation = {
  id: "deriv-1",
  kind: "hypothesis",
  payload: {
    stages: [
      { name: "generated", count: 24, ids: ["h-1", "h-2"] },
      { name: "distinct", count: 1, ids: ["h-1"] },
    ],
  },
  parent_ids: [],
  run_id: "repair_runs:repair-abc-123",
  created_at: new Date().toISOString(),
};

const mockLedger = {
  repair_run_id: "repair-abc-123",
  decision: "propose",
  resolved_rung: 1,
  attempts: [
    { rung: 0, action: "knowledge lookup", reason: "L0 miss: no known fix", calls: [] },
    { rung: 1, action: "rule page_to_cursor matched", reason: "wire shape matches", calls: [] },
  ],
  total_calls: 0,
  small_tokens: 0,
  frontier_tokens: 0,
  total_cost: "0",
};

const mockConnector = {
  id: "c1",
  name: "AtlasPMS",
  state: "degrading",
  activity: "repair waiting",
  target_entities: ["Reservation"],
};

const mockSyncRun = {
  kind: "sync",
  payload: {
    sync_run: {
      id: "sync-1",
      release_id: "rel-1",
      started_at: new Date(Date.now() - 5000).toISOString(),
      finished_at: new Date().toISOString(),
      records_observed: { Reservation: 4 },
      records_emitted: { Reservation: 4 },
      outcome: "Degraded",
      unmet_criteria: ["records_match"],
    },
  },
};

const mockModelUsage = {
  total_calls: 2,
  total_input_tokens: 800,
  total_output_tokens: 400,
  total_cost: "0.01",
  frontier_tokens: 900,
  small_tokens: 300,
  by_tier: { frontier: 900, small: 300 },
  by_provider: { anthropic: 2 },
  by_purpose: { rank: 1, patch: 1 },
  entries: [
    {
      provider: "anthropic",
      model: "claude-haiku",
      tier: "small",
      total_calls: 1,
      total_input_tokens: 200,
      total_output_tokens: 100,
      total_cost: "0.001",
      by_run: [{ run_id: "repair-abc-123", purpose: "rank", tokens: 300 }],
    },
    {
      provider: "anthropic",
      model: "claude-opus",
      tier: "frontier",
      total_calls: 1,
      total_input_tokens: 600,
      total_output_tokens: 300,
      total_cost: "0.009",
      by_run: [{ run_id: "repair-abc-123", purpose: "patch", tokens: 900 }],
    },
  ],
};

function mockAll(repair: unknown = mockRepair, ledger: unknown = mockLedger) {
  mockedUseSWR
    .mockReturnValueOnce({ data: repair, error: undefined, isLoading: false, mutate: vi.fn() })
    .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: [mockEvidence], error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: [mockDerivation], error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: ledger, error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: mockConnector, error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: mockSyncRun, error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: { rules: [{ id: "page_to_cursor", fixes: "page→cursor", confidence: 0.95 }] }, error: undefined, isLoading: false })
    .mockReturnValueOnce({ data: mockModelUsage, error: undefined, isLoading: false })
    .mockReturnValue({ data: undefined, error: undefined, isLoading: false });
}

describe("RepairDetailPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows loading when params is Promise", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={Promise.resolve({ id: "repair-abc-123" })} />);
    expect(screen.getByTestId("repair-detail-page")).toBeInTheDocument();
  });

  it("renders the decision line and actions at the top", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Fix ready: verified, 0 model calls");
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-drawer")).toBeInTheDocument();
  });

  it("answers what happened from persisted rows", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    const section = screen.getByTestId("repair-section-happened");
    expect(section).toBeInTheDocument();
    expect(screen.getByTestId("happened-connector")).toHaveTextContent("AtlasPMS");
    expect(screen.getByTestId("happened-sync-outcome")).toHaveTextContent("Degraded");
    expect(screen.getByTestId("happened-sync-outcome")).toHaveTextContent("records_match");
    expect(screen.getByTestId("wire-change-line-0")).toHaveTextContent("page → cursor");
    expect(screen.getByTestId("happened-affects")).toHaveTextContent("Reservation");
    expect(screen.getByTestId("drift-evidence-section")).toBeInTheDocument();
  });

  it("answers what the engine did: rungs, funnel, probes, model calls, verifier", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-section-did")).toBeInTheDocument();
    expect(screen.getByTestId("rung-timeline-0")).toHaveTextContent("L0 miss");
    expect(screen.getByTestId("rung-timeline-1")).toHaveTextContent("page_to_cursor");
    expect(screen.getByTestId("hypothesis-funnel-headline")).toHaveTextContent("24 → 1");
    expect(screen.getByTestId("repair-probe-0")).toHaveTextContent("GET /reservations");
    expect(screen.getByTestId("model-call-box-empty")).toHaveTextContent("0 model calls");
    expect(screen.getByTestId("verifier-checks-line")).toHaveTextContent("1/1 checks pass");
  });

  it("answers what the engine decided: patch diff, touches, raw patch", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-section-decided")).toBeInTheDocument();
    expect(screen.getByTestId("decided-line")).toHaveTextContent("Fix ready");
    expect(screen.getByTestId("patch-line-0")).toHaveTextContent("set /operations/list/pagination/strategy");
    expect(screen.getByTestId("patch-touches")).toHaveTextContent("touches: operations");
    expect(screen.getByTestId("patch-raw")).toHaveTextContent("cursor");
    expect(screen.getByTestId("patch-diff")).toBeInTheDocument();
  });

  it("answers what approve will do from the server preview", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-section-approve")).toBeInTheDocument();
    // ApprovePreviewSection fetches the preview endpoint (last SWR mock).
    expect(screen.getByTestId("approve-preview-loading")).toBeInTheDocument();
  });

  it("shows the escalation question and rejected proposals for escalations", () => {    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [{ level: 3, action: "proposal_rejected", reason: "patch does not apply" }],
      escalation_bundle: { reason: "no verified fix", question: "Which mapping is correct?", suggested_action: "pick one" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Needs your decision");
    expect(screen.getByTestId("decided-question")).toHaveTextContent("Which mapping is correct?");
    expect(screen.getByTestId("decided-rejected")).toHaveTextContent("proposal_rejected");
  });

  it("shows the running view while the climb is in flight", () => {
    const climbingLedger = {
      ...mockLedger,
      decision: "climbing",
      resolved_rung: 3,
      attempts: [
        { rung: 0, action: "knowledge_miss", reason: "L0 miss: no known fix", calls: [] },
        { rung: 1, action: "no_rule_matched", reason: "L1: no rule precondition matched", calls: [] },
        { rung: 2, action: "l2_skipped", reason: "L2 skipped: fewer than two hypotheses (1)", calls: [] },
        { rung: 3, action: "frontier_generate", reason: "L3 invoked", calls: [] },
      ],
    };
    mockAll(
      {
        ...mockRepair,
      decision: "climbing",
      ladder_level_reached: 2,
      verifier_report_id: null,
      candidate_patch: [],
      rule_id: null,
      model_usage: {},
      ladder_trace: [
        { level: 0, action: "knowledge_miss", reason: "L0 miss: no known fix" },
        { level: 1, action: "no_rule_matched", reason: "L1: no rule precondition matched" },
        { level: 2, action: "l2_skipped", reason: "L2 skipped: fewer than two hypotheses (1)" },
        {
          level: 3,
          action: "frontier_generate",
          reason: "L3 invoked",
          model: "sleep-frontier",
          call_started_at: new Date(Date.now() - 45000).toISOString(),
        },
      ],
      },
      climbingLedger,
    );
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-detail-page")).toHaveAttribute("data-state", "climbing");
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Repairing at L3");
    expect(screen.getByTestId("repair-calling")).toHaveTextContent("calling sleep-frontier");
    expect(screen.getByTestId("repair-calling")).toHaveTextContent("so far");
    expect(screen.getByTestId("model-call-box-empty")).toHaveTextContent("no calls landed yet");
    expect(screen.getByTestId("rung-timeline-3")).toHaveTextContent("L3 invoked");
  });

  it("names a failed climb without offering approve", () => {
    mockAll({
      ...mockRepair,
      decision: "failed",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [{ level: 3, action: "failed", reason: "RuntimeError: boom" }],
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Repair failed");
  });

  it("survives the loading-to-loaded transition without a hooks crash", () => {
    // First render loading (SWR fetching), then data: hook order must be
    // stable across the transition or React throws "rendered more hooks".
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    const { rerender } = render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-detail-page")).toHaveTextContent("Loading repair");
    mockAll();
    rerender(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Fix ready");
  });

  it("keeps the evidence graph appendix", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("evidence-graph")).toBeInTheDocument();
  });

  it("restores the per-run model-usage chart appendix with the repair highlighted", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    const chart = screen.getByTestId("model-usage-chart");
    expect(chart).toBeInTheDocument();
    const row = screen.getByTestId("model-usage-chart-row");
    expect(row).toHaveAttribute("data-run-id", "repair-abc-123");
    expect(row).toHaveAttribute("data-highlight", "true");
  });

  it("labels the escalation question as the model's when L3 escalated explicitly", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [
        { level: 3, action: "model_escalation", reason: "L3 asked for a human decision: Should total_amount track gross or net?", model_question: "Should total_amount track gross or net?" },
        { level: 4, action: "escalate", reason: "L3 asked for a human decision (gross_to_net): Should total_amount track gross or net?" },
      ],
      escalation_bundle: { reason: "L3 asked for a human decision", question: "Should total_amount track gross or net?", suggested_action: "pick one" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("decided-question")).toHaveTextContent("The model's question for you:");
    expect(screen.getByTestId("decided-question")).toHaveTextContent("Should total_amount track gross or net?");
  });

  it("shows the model rationale beneath its question (REQ-047)", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [
        { level: 3, action: "model_escalation", reason: "L3 asked for a human decision", model_question: "Should total_amount track gross or net?", model_rationale: "Net versus gross is a business call." },
        { level: 4, action: "escalate", reason: "L3 asked for a human decision" },
      ],
      escalation_bundle: { reason: "gross_to_net: no fix; model rationale: Net versus gross is a business call.", question: "Should total_amount track gross or net?", suggested_action: "pick one" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("decided-question")).toHaveTextContent("The model's question for you:");
    expect(screen.getByTestId("decided-rationale")).toHaveTextContent("Net versus gross is a business call.");
  });
});
