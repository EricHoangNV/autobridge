import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import { HypothesisFunnel } from "./HypothesisFunnel";
import { ModelCallBox } from "./ModelCallBox";
import { ApprovePreviewSection } from "./ApprovePreview";
import { AfterApproveTracker } from "./AfterApproveTracker";
import { PabxPanel } from "./PabxPanel";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => vi.resetAllMocks());

describe("HypothesisFunnel", () => {
  const stages = [
    { name: "generated", count: 24, ids: ["h-1", "h-2"] },
    { name: "about amounts", count: 6, ids: ["h-1"] },
    { name: "wire-consistent", count: 2, ids: ["h-1", "h-3"] },
    { name: "distinct", count: 1, ids: ["h-1"], families: ["rename"] },
  ];

  it("renders the funnel headline and expandable stages", () => {
    render(<HypothesisFunnel stages={stages} />);
    expect(screen.getByTestId("hypothesis-funnel-headline")).toHaveTextContent("24 → 6 → 2 → 1");
    const toggle = screen.getByTestId("funnel-stage-toggle-distinct");
    expect(screen.queryByTestId("funnel-stage-detail-distinct")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByTestId("funnel-stage-detail-distinct")).toHaveTextContent("h-1");
  });

  it("is honest when no derivation was recorded", () => {
    render(<HypothesisFunnel stages={[]} />);
    expect(screen.getByTestId("hypothesis-funnel-empty")).toBeInTheDocument();
  });
});

describe("ModelCallBox", () => {
  it("says 0 model calls explicitly when there were none", () => {
    render(<ModelCallBox attempts={[{ rung: 1, action: "rule matched", reason: "precondition", calls: [] }]} />);
    expect(screen.getByTestId("model-call-box-empty")).toHaveTextContent("0 model calls");
  });

  it("lists every call with rung, model, live/replay, tokens, cost, outcome", () => {
    render(
      <ModelCallBox
        attempts={[
          {
            rung: 3,
            action: "frontier proposal",
            reason: "L2 not confident",
            calls: [
              {
                tier: "frontier",
                purpose: "proposal",
                model: "muse-spark-1.3",
                mode: "replay",
                captured_at: "2026-10-28",
                outcome: "proposal_rejected",
                input_tokens: 1000,
                output_tokens: 200,
                reasoning_tokens: 50,
                cost: "0.0056",
              },
            ],
          },
        ]}
      />,
    );
    const row = screen.getByTestId("model-call-row-0");
    expect(row).toHaveTextContent("L3");
    expect(row).toHaveTextContent("muse-spark-1.3");
    expect(row).toHaveTextContent("replayed 2026-10-28");
    expect(row).toHaveTextContent("1,000/200/50");
    expect(row).toHaveTextContent("proposal_rejected");
    expect(screen.getByTestId("model-call-box-total")).toHaveTextContent("1 call");
  });
});

describe("ApprovePreviewSection", () => {
  it("renders approve and reject lines from the server", () => {
    mockedUseSWR.mockReturnValue({
      data: {
        approvable: true,
        reason: "",
        approve_lines: ["Creates release r4 from r3 with this change.", "Activates r4."],
        reject_lines: ["The drift stays open."],
        facts: {},
      },
      error: undefined,
    });
    render(<ApprovePreviewSection repairId="rep-1" />);
    expect(screen.getByTestId("approve-preview-line-0")).toHaveTextContent("Creates release r4 from r3");
    expect(screen.getByTestId("reject-preview-line-0")).toHaveTextContent("drift stays open");
  });

  it("shows the server reason when not approvable", () => {
    mockedUseSWR.mockReturnValue({
      data: { approvable: false, reason: "No candidate release for this repair", approve_lines: [], reject_lines: [], facts: {} },
      error: undefined,
    });
    render(<ApprovePreviewSection repairId="rep-1" />);
    expect(screen.getByTestId("approve-preview-blocked")).toHaveTextContent("No candidate release");
  });
});

describe("AfterApproveTracker", () => {
  function mockAll(repair: unknown, connector: unknown, syncs: unknown) {
    mockedUseSWR
      .mockReturnValueOnce({ data: repair })
      .mockReturnValueOnce({ data: connector })
      .mockReturnValueOnce({ data: syncs });
  }

  it("renders nothing before approval", () => {
    mockAll({ id: "rep-1", approval: null }, null, []);
    const { container } = render(<AfterApproveTracker repairId="rep-1" connectorId="c1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("follows approval → activation → sync → working", () => {
    mockAll(
      { id: "rep-1", approval: { id: "a", actor: "operator", decision: "approve", to_release_id: "rel-2", created_at: "t" } },
      { id: "c1", active_release_id: "rel-2", state: "working", activity: "all clear" },
      [{ id: "s1", release_id: "rel-2", outcome: "Complete", started_at: "t2" }],
    );
    render(<AfterApproveTracker repairId="rep-1" connectorId="c1" />);
    expect(screen.getByTestId("after-approve-step-approval")).toHaveAttribute("data-done", "yes");
    expect(screen.getByTestId("after-approve-step-activated")).toHaveAttribute("data-done", "yes");
    expect(screen.getByTestId("after-approve-step-sync")).toHaveTextContent("Complete");
    expect(screen.getByTestId("after-approve-step-working")).toHaveAttribute("data-done", "yes");
  });
});

describe("PabxPanel", () => {
  function mockAll(events: unknown, agents: unknown, postings: unknown) {
    mockedUseSWR
      .mockReturnValueOnce({ data: events })
      .mockReturnValueOnce({ data: agents })
      .mockReturnValueOnce({ data: postings, error: undefined });
  }

  it("shows link, postings, layout and restart from persisted rows", () => {
    mockAll(
      [
        { id: "e1", connector_id: "c-pabx", kind: "link", payload: { link_up: true, link_detail: "ok layouts={\"PS\": [\"TA\"]}" }, created_at: "t" },
        { id: "e2", connector_id: "c-pabx", kind: "restart", payload: { redelivered: 1, lost: 0, duplicated: 0 }, created_at: "t" },
      ],
      { agents: [{ agent_id: "c-pabx", link_up: true }] },
      { postings: [{ idempotency_key: "k", agent_id: "c-pabx", record_type: "PS", received_at: new Date().toISOString() }] },
    );
    render(<PabxPanel connectorId="c-pabx" connectorName="Grand Plaza" />);
    expect(screen.getByTestId("pabx-panel")).toBeInTheDocument();
    expect(screen.getByTestId("pabx-link-state")).toHaveTextContent("up");
    expect(screen.getByTestId("pabx-postings")).toHaveTextContent("1 received");
    expect(screen.getByTestId("pabx-layout")).toHaveTextContent("PS");
    expect(screen.getByTestId("pabx-restart")).toHaveTextContent("redelivered 1 · lost 0 · duplicated 0");
  });

  it("is honest when nothing is announced yet", () => {
    mockAll([], { agents: [] }, { postings: [] });
    render(<PabxPanel connectorId="c-pabx" connectorName="Grand Plaza" />);
    expect(screen.getByTestId("pabx-link-state")).toHaveTextContent("unknown yet");
    expect(screen.getByTestId("pabx-layout")).toHaveTextContent("not announced yet");
    expect(screen.getByTestId("pabx-restart")).toHaveTextContent("no restart recorded");
  });
});
