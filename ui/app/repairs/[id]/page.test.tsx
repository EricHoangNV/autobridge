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

const mockConnectorWithSpec = {
  ...mockConnector,
  active_release_id: "rel-1",
  releases: [
    {
      id: "rel-1",
      version: 1,
      spec: { operations: { list_reservations: { pagination: { strategy: "page", page_param: "page" } } } },
    },
  ],
};

const mockRepairRename = {
  ...mockRepair,
  candidate_patch: [{ op: "replace", path: "/operations/list_reservations/pagination/strategy", value: "cursor" }],
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

function mockAll(repair: unknown = mockRepair, ledger: unknown = mockLedger, runEvidence: unknown = [mockDerivation], connector: unknown = mockConnector, evidence: unknown = [mockEvidence]) {
  const repairId = (repair as { id: string }).id;
  mockedUseSWR.mockImplementation(((key: unknown) => {
    const ok = (data: unknown) => ({ data, error: undefined, isLoading: false, mutate: vi.fn() });
    if (typeof key !== "string") return { data: undefined, error: undefined, isLoading: false, mutate: vi.fn() };
    if (key === `/repairs/${encodeURIComponent(repairId)}`) return ok(repair);
    if (key === `/repairs/${encodeURIComponent(repairId)}/ledger`) return ok(ledger);
    if (key.startsWith("drift-for-repair-")) return ok([mockDrift]);
    if (key.startsWith("evidence-for-repair-")) return ok(evidence);
    if (key.endsWith("/evidence")) return ok(runEvidence);
    if (key.startsWith("/runs/")) return ok(mockSyncRun);
    if (key.endsWith("/sync-runs")) return ok([]);
    if (key.startsWith("/connectors/")) return ok(connector);
    if (key === "/capabilities") return ok({ rules: [{ id: "page_to_cursor", fixes: "page→cursor", confidence: 0.95 }] });
    if (key === "/metrics/model-usage") return ok(mockModelUsage);
    // ApprovePreviewSection, AfterApproveTracker and anything else: no data.
    return { data: undefined, error: undefined, isLoading: false, mutate: vi.fn() };
  }) as unknown as typeof useSWR);
}

describe("RepairDetailPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows loading when params is Promise", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={Promise.resolve({ id: "repair-abc-123" })} />);
    expect(screen.getByTestId("repair-detail-page")).toBeInTheDocument();
  });

  it("renders the headline, storyline and the decision rail", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-headline")).toHaveTextContent("AtlasPMS");
    expect(screen.getByTestId("repair-headline")).toHaveTextContent("fixed at L1");
    // Storyline: 1 happened → 2 did → 3 recommends, mark on step 3.
    expect(screen.getByTestId("storyline-1")).toHaveTextContent("1 · What happened");
    expect(screen.getByTestId("storyline-2")).toHaveTextContent("2 · What the engine did");
    expect(screen.getByTestId("storyline-3")).toHaveTextContent("3 · Recommendation");
    expect(screen.getByTestId("storyline-3")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("storyline-1").querySelector("a")).toHaveAttribute("href", "#happened");
    expect(screen.getByTestId("storyline-2").querySelector("a")).toHaveAttribute("href", "#did");
    expect(screen.getByTestId("storyline-3").querySelector("a")).toHaveAttribute("href", "#recommends");
    // Decision rail carries the approve block (data-testid moved with it).
    expect(screen.getByTestId("repair-section-approve")).toBeInTheDocument();
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-drawer")).toBeInTheDocument();
    // Mobile sticky bar renders the same summary.
    expect(screen.getByTestId("repair-sticky-summary")).toHaveTextContent("Fix verified");
  });

  it("renders the decision line without repeating the model cost", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Fix ready: verified");
    expect(screen.getByTestId("repair-decision-line")).not.toHaveTextContent("model calls");
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

  it("answers what the engine did: rungs, funnel, probes, proof tiles, verifier", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-section-did")).toBeInTheDocument();
    expect(screen.getByTestId("rung-timeline-0")).toHaveTextContent("L0 miss");
    expect(screen.getByTestId("rung-timeline-1")).toHaveTextContent("page_to_cursor");
    // Unused rungs shrink to one line.
    expect(screen.getByTestId("rung-timeline-2")).toHaveTextContent("Not needed");
    expect(screen.getByTestId("hypothesis-funnel-headline")).toHaveTextContent("24 → 1");
    expect(screen.getByTestId("repair-probe-0")).toHaveTextContent("GET /reservations");
    // Model cost is a single tile; with no calls the ledger box is omitted.
    expect(screen.getByTestId("proof-model-calls")).toHaveTextContent("0");
    expect(screen.queryByTestId("model-call-box-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("model-call-box")).not.toBeInTheDocument();
    expect(screen.getByTestId("proof-verifier")).toBeInTheDocument();
    expect(screen.getByTestId("verifier-checks-line")).toHaveTextContent("1/1 checks pass");
  });

  it("shows the tile when the rule needed no enumeration (REQ-054)", () => {
    mockAll(mockRepair, mockLedger, [], mockConnector, []);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("proof-hypotheses")).toHaveTextContent("The rule needed no enumeration");
    expect(screen.queryByTestId("hypothesis-funnel")).not.toBeInTheDocument();
  });

  it("renders the Now → Proposed table grouped by operation (REQ-054)", () => {
    mockAll(mockRepairRename, mockLedger, [mockDerivation], mockConnectorWithSpec);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-section-decided")).toBeInTheDocument();
    expect(screen.getByTestId("decided-line")).toHaveTextContent("Fix ready");
    // Grouped by operation, one row per op.
    expect(screen.getByTestId("op-group-0")).toHaveTextContent("list_reservations");
    expect(screen.getByTestId("op-row-0")).toHaveTextContent("/pagination/strategy");
    // Now values come from the active spec, never placeholders.
    expect(screen.getByTestId("op-now-0")).toHaveTextContent('"page"');
    expect(screen.getByTestId("op-proposed-0")).toHaveTextContent('"cursor"');
    expect(screen.getByTestId("patch-touches")).toHaveTextContent("touches: operations");
    expect(screen.getByTestId("patch-raw")).toHaveTextContent("cursor");
    expect(screen.getByTestId("patch-copy")).toBeInTheDocument();
  });

  it("shows unset Now values when the connector has no active spec yet", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("op-now-0")).toHaveTextContent("—");
    expect(screen.getByTestId("op-proposed-0")).toHaveTextContent('"cursor"');
  });

  it("answers what approve will do from the server preview, inside the rail", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-section-approve")).toBeInTheDocument();
    // ApprovePreviewSection fetches the preview endpoint (last SWR mock).
    expect(screen.getByTestId("approve-preview-loading")).toBeInTheDocument();
  });

  it("shows the escalation question and rejected proposals in the rail", () => {    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [{ level: 3, action: "proposal_rejected", reason: "patch does not apply" }],
      escalation_bundle: { reason: "no verified fix", question: "Which mapping is correct?", suggested_action: "pick one" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Needs your decision");
    // REQ-048: the question lives once, first, in the rail.
    expect(screen.getByTestId("repair-section-approve")).toContainElement(screen.getByTestId("escalation-question-top"));
    expect(screen.getByTestId("escalation-question-top")).toHaveTextContent("Which mapping is correct?");
    expect(screen.getAllByText("Which mapping is correct?")).toHaveLength(1);
    expect(screen.getByTestId("decided-rejected")).toHaveTextContent("proposal_rejected");
  });

  it("escalate rail has no Approve button and a Deny that closes it (REQ-048, REQ-054)", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [{ level: 4, action: "escalate", reason: "no verified fix" }],
      escalation_bundle: { reason: "no verified fix", question: "Which mapping is correct?", suggested_action: "pick one" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.queryByTestId("repair-approve-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-approve-proposal-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    expect(screen.queryByText(/Approve\/Reject available only when decision is/)).not.toBeInTheDocument();
    // Deny closes it; roll back stays available.
    const deny = screen.getByTestId("repair-deny-btn");
    expect(deny).not.toBeDisabled();
    expect(deny).toHaveTextContent(/Deny: keep everything-as-is|Deny: keep everything as-is/);
    expect(screen.getByTestId("rollback-btn")).toBeInTheDocument();
    expect(screen.getByTestId("repair-section-approve")).toHaveTextContent("or Deny below to close this repair");
  });

  it("climbing uses the same layout: step 2 active, rail shows the run, no actions (REQ-054)", () => {
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
    expect(screen.getByTestId("storyline-2")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("storyline-3")).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Repairing at L3");
    // Rail shows the run, not actions.
    expect(screen.getByTestId("repair-climbing-status")).toHaveTextContent("calling sleep-frontier");
    expect(screen.getByTestId("repair-climbing-status")).toHaveTextContent("so far");
    expect(screen.queryByTestId("repair-approve-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-deny-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("repair-calling")).toHaveTextContent("so far");
    expect(screen.getByTestId("model-call-box-empty")).toHaveTextContent("no calls landed yet");
    expect(screen.getByTestId("rung-timeline-3")).toHaveTextContent("L3 invoked");
  });

  it("names a failed climb in the rail with actions disabled", () => {
    mockAll({
      ...mockRepair,
      decision: "failed",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [{ level: 3, action: "failed", reason: "RuntimeError: boom" }],
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Repair failed");
    expect(screen.getByTestId("repair-outcome")).toHaveTextContent("No approval record");
    const failed = screen.getByTestId("repair-approve-btn");
    expect(failed).toBeDisabled();
    expect(failed).toHaveTextContent("Failed");
  });

  it("rejected repairs show the outcome with actions disabled", () => {
    mockAll({
      ...mockRepair,
      decision: "reject",
      verifier_report_id: "report-xyz",
      approval: { id: "ap-9", actor: "bob", decision: "reject", from_release_id: "r1", to_release_id: null, created_at: new Date().toISOString() },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Rejected");
    expect(screen.getByTestId("repair-outcome")).toHaveTextContent("Rejected by bob");
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

  it("keeps the evidence graph appendix collapsed in chapter 2", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("evidence-graph-panel")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-graph")).toBeInTheDocument();
  });

  it("restores the per-run model-usage chart collapsed in chapter 2", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    const chart = screen.getByTestId("model-usage-chart");
    expect(chart).toBeInTheDocument();
    const row = screen.getByTestId("model-usage-chart-row");
    expect(row).toHaveAttribute("data-run-id", "repair-abc-123");
    expect(row).toHaveAttribute("data-highlight", "true");
  });

  it("labels the escalation question as the model's when L3 escalated explicitly", () => {
    const question = "Should total_amount track gross or net?";
    mockAll(
      {
        ...mockRepair,
        decision: "escalate",
        verifier_report_id: null,
        candidate_patch: [],
        ladder_trace: [
          { level: 3, action: "model_escalation", reason: `L3 asked for a human decision: ${question}`, model_question: question },
          { level: 4, action: "escalate", reason: `L3 asked for a human decision (gross_to_net): ${question}` },
        ],
        escalation_bundle: { reason: "L3 asked for a human decision", question, suggested_action: "pick one" },
      },
      {
        ...mockLedger,
        decision: "escalate",
        resolved_rung: 4,
        attempts: [
          { rung: 3, action: "model_escalation", reason: `L3 asked for a human decision: ${question}`, calls: [] },
          { rung: 4, action: "escalate", reason: `L3 asked for a human decision (gross_to_net): ${question}`, calls: [] },
        ],
      },
    );
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    // REQ-048: the question lives once, in the rail.
    expect(screen.getByTestId("escalation-question-top")).toHaveTextContent("The model's question for you:");
    expect(screen.getByTestId("escalation-question-top")).toHaveTextContent(question);
    expect(screen.getAllByText(question)).toHaveLength(1);
    // Rungs name the handoff instead of repeating the question.
    expect(screen.getByTestId("rung-timeline-3")).toHaveTextContent("the model asked for a human decision");
    expect(screen.getByTestId("rung-timeline-3")).not.toHaveTextContent(question);
    expect(screen.getByTestId("rung-timeline-4")).toHaveTextContent("escalated to you");
    expect(screen.getByTestId("rung-timeline-4")).not.toHaveTextContent(question);
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
    expect(screen.getByTestId("escalation-question-top")).toHaveTextContent("The model's question for you:");
    expect(screen.getByTestId("escalation-rationale-top")).toHaveTextContent("Net versus gross is a business call.");
    // REQ-048: the rationale is printed once — the bundle box strips the clause.
    expect(screen.getByTestId("escalation-reason")).not.toHaveTextContent("Net versus gross is a business call.");
  });

  it("shows the running funnel as deriving while the climb runs (REQ-048)", () => {
    mockAll(
      {
        ...mockRepair,
        decision: "climbing",
        ladder_level_reached: 1,
        verifier_report_id: null,
        candidate_patch: [],
        rule_id: null,
        model_usage: {},
        ladder_trace: [{ level: 1, action: "no_rule_matched", reason: "L1: no rule precondition matched" }],
      },
      { ...mockLedger, decision: "climbing", resolved_rung: 1, attempts: [] },
      [],
    );
    // No derivation evidence linked: the fourth SWR mock returns [] here.
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("hypothesis-funnel-empty")).toHaveTextContent("deriving…");
  });

  it("labels a BudgetExhausted climb on the page (REQ-048)", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [
        { level: 3, action: "frontier_generate", reason: "BudgetExhausted in L3: max_frontier_tokens 131072 exceeded (now 131100)" },
        { level: 4, action: "escalate", reason: "BudgetExhausted in L3: max_frontier_tokens 131072 exceeded (now 131100)" },
      ],
      escalation_bundle: { reason: "budget ran out", question: "Which mapping?", suggested_action: "retry later" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-budget-exhausted")).toHaveTextContent("Budget exhausted");
  });

  it("shows the frontier budget that applies, never a hard-coded default (REQ-048)", () => {
    mockAll({
      ...mockRepair,
      model_usage: { frontier_tokens: 3530, small_tokens: 0, calls: 1, frontier_budget: 131072 },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("proof-model-calls")).toHaveTextContent("of 131,072 budget");
  });

  it("escalation with a model proposal shows the effect, the diff and Approve/Deny (REQ-049)", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [
        { level: 3, action: "model_escalation", reason: "L3 asked for a human decision", model_question: "Should the new behaviour stand?" },
        { level: 4, action: "escalate", reason: "L3 asked for a human decision (gross_to_net)" },
      ],
      escalation_bundle: {
        reason: "gross_to_net: semantic drift, no verified fix",
        question: "Should the new behaviour stand?",
        suggested_action: "manual review",
        proposal: {
          label: "Accept net",
          effect: "Retarget the totals check to the page total.",
          patch: [{ op: "replace", path: "/entities/Reservation/checks/2/compare_to", value: "$.total" }],
        },
      },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    // Question once, first, in the rail — plus the proposal's effect and diff.
    expect(screen.getByTestId("escalation-question-top")).toHaveTextContent("Should the new behaviour stand?");
    expect(screen.getByTestId("escalation-proposal-top")).toHaveTextContent("Retarget the totals check");
    expect(screen.getByTestId("escalation-proposal-top")).toHaveTextContent("/entities/Reservation/checks/2/compare_to");
    // Two actions only: Approve with the model's label, and Deny.
    expect(screen.getByTestId("repair-approve-proposal-btn")).toHaveTextContent("Approve: Accept net");
    expect(screen.getByTestId("repair-deny-btn")).toHaveTextContent(/Deny: keep everything as-is/);
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-approve-btn")).not.toBeInTheDocument();
  });

  it("escalation without a proposal keeps Deny and no Approve (REQ-054)", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [{ level: 4, action: "escalate", reason: "no verified fix" }],
      escalation_bundle: { reason: "no verified fix", question: "Which mapping is correct?", suggested_action: "pick one" },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.queryByTestId("repair-approve-proposal-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("repair-deny-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
  });

  it("approved escalation reads as a business decision, not a verified fix (REQ-049)", () => {
    mockAll({
      ...mockRepair,
      decision: "escalate",
      verifier_report_id: "report-2",
      candidate_patch: [],
      ladder_trace: [
        { level: 3, action: "model_escalation", reason: "L3 asked for a human decision", model_question: "Should the new behaviour stand?" },
        { level: 4, action: "escalate", reason: "L3 asked for a human decision" },
      ],
      escalation_bundle: {
        reason: "gross_to_net: semantic drift, no verified fix",
        question: "Should the new behaviour stand?",
        suggested_action: "manual review",
        proposal: {
          label: "Accept net",
          effect: "Retarget the totals check.",
          patch: [{ op: "replace", path: "/entities/Reservation/checks/2/compare_to", value: "$.total" }],
        },
      },
      approval: { id: "ap-1", actor: "alice", decision: "approve", from_release_id: "r1", to_release_id: "r2", created_at: new Date().toISOString() },
    });
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-decision-line")).toHaveTextContent("Business decision by alice");
    expect(screen.getByTestId("repair-decision-line")).not.toHaveTextContent("Fix ready: verified");
    expect(screen.getByTestId("repair-outcome")).toHaveTextContent("Approved by alice");
  });

  it("renders each fact once: one model-cost tile, one patch table (REQ-054)", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    // "model calls" reads once (the tile label); the ledger box is omitted.
    expect(screen.getAllByText(/model calls/i)).toHaveLength(1);
    // The patch reads once as the table; the raw JSON stays collapsed.
    expect(screen.getByTestId("op-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("patch-raw")).toBeInTheDocument();
  });

  it("animates the storyline by default and skips motion when reduced (REQ-054)", () => {
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("storyline")).toHaveAttribute("data-animate", "on");
    expect(screen.getByTestId("repair-section-approve")).toHaveAttribute("data-animate", "on");
  });

  it("draws storyline bars full with reduced motion (REQ-054)", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    );
    mockAll();
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("storyline")).toHaveAttribute("data-animate", "off");
    expect(screen.getByTestId("repair-section-approve")).toHaveAttribute("data-animate", "off");
    vi.unstubAllGlobals();
  });
});
