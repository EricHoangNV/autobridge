import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import KnowledgePage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

const CAPABILITIES = {
  versions: {
    v1: { methods: ["GET"], auth_types: ["bearer"], pagination_strategies: ["page"], protocols: ["http"], joins: false, body_templates: false, event_feeds: false, command_templates: false },
    v2: { methods: ["POST"], auth_types: ["body_fields"], pagination_strategies: ["body_cursor"], protocols: [], joins: true, body_templates: true, event_feeds: false, command_templates: false },
    v3: { methods: ["EVENT"], auth_types: ["edge_relay"], pagination_strategies: [], protocols: ["fias_tcp"], joins: false, body_templates: false, event_feeds: true, command_templates: true },
  },
  transforms: ["money"],
  checks: ["money_invariant"],
  stop_conditions: ["null_cursor"],
  rules: [{ id: "page_to_cursor", fixes: "flip page to cursor", confidence: 0.94, verification_level: "visible", produces_patch: true }],
  v1_v2_diff: ["POST ops"],
  v2_v3_diff: ["EVENT ops"],
  tools: [{ name: "reset_demo", description: "reset", inputSchema: { type: "object" } }],
};

const KNOWLEDGE = {
  entries: [
    {
      vendor: "AtlasPMS",
      drift_class: "PAGINATION_CHANGE",
      drift_signature: "abc",
      rule_id: "page_to_cursor",
      patch: [],
      learned_from: { run_id: "r1", rung: 1, tokens: 0, cost: "0", at: "2026-01-01T00:00:00Z", approval_id: "a1", actor: "tester", release_id: "rel2" },
      times_reused: 1,
      tokens_saved: 0,
      cost_saved: "0",
      approvals: 1,
    },
  ],
  connectors: [
    { connector_id: "c1", name: "Atlas", vocabulary: "v1", refused_vocabularies: [], builds: 1, confirmed_hypotheses: ["pagination:/reservations:cursor"], last_built_at: null },
    { connector_id: "c2", name: "meridianpms", vocabulary: "v2", refused_vocabularies: ["v1"], builds: 2, confirmed_hypotheses: [], last_built_at: null },
    { connector_id: "c3", name: "PABX edge", vocabulary: "v3", refused_vocabularies: [], builds: 1, confirmed_hypotheses: [], last_built_at: null },
  ],
  counts: { entries: 1, reuses: 1, tokens_saved: 0 },
};

function mockBoth(capabilities: unknown, knowledge: unknown) {
  mockedUseSWR
    .mockReturnValueOnce({ data: capabilities, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
    .mockReturnValueOnce({ data: knowledge, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>);
}

describe("KnowledgePage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows both sections: vocabulary per version and learned knowledge", () => {
    mockBoth(CAPABILITIES, KNOWLEDGE);
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-vocabulary-v1").textContent).toContain("methods: GET");
    expect(screen.getByTestId("knowledge-vocabulary-v2").textContent).toContain("POST");
    expect(screen.getByTestId("knowledge-vocabulary-v2").textContent).toContain("everything in v1, plus:");
    expect(screen.getByTestId("knowledge-vocabulary-v3").textContent).toContain("EVENT");
    expect(screen.getByTestId("knowledge-vocabulary-v3").textContent).toContain("everything in v2, plus:");
    expect(screen.getByTestId("knowledge-rule-page_to_cursor").textContent).toContain("flip page to cursor");
    expect(screen.getByTestId("knowledge-entry-0").textContent).toContain("AtlasPMS / PAGINATION_CHANGE");
    expect(screen.getByTestId("knowledge-entry-0").textContent).toContain("reused 1x");
    expect(screen.getByTestId("knowledge-connector-c1").textContent).toContain("Atlas");
  });

  it("shows the refused vocabulary beside the release in use", () => {
    mockBoth(CAPABILITIES, KNOWLEDGE);
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-connector-c2").textContent).toContain("meridianpms");
    expect(screen.getByTestId("knowledge-connector-c2").textContent).toContain("vocabulary v2");
    expect(screen.getByTestId("knowledge-connector-c2").textContent).toContain("v1 refused");
    expect(screen.getByTestId("knowledge-connector-c3").textContent).toContain("PABX edge");
    expect(screen.getByTestId("knowledge-connector-c3").textContent).toContain("vocabulary v3");
  });

  it("shows which approval taught each learned fix", () => {
    mockBoth(CAPABILITIES, KNOWLEDGE);
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-entry-0").textContent).toContain("learned from approval a1 by tester");
  });

  it("shows the empty state when nothing is learned", () => {
    mockBoth(CAPABILITIES, { entries: [], connectors: [], counts: {} });
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-learned-empty")).toBeInTheDocument();
    // REQ-059 blank slate: every vocabulary reads "no connector built on it yet".
    expect(screen.getByTestId("knowledge-vocabulary-v1-used-by")).toHaveTextContent("no connector built on it yet");
    expect(screen.getByTestId("knowledge-vocabulary-v2-used-by")).toHaveTextContent("no connector built on it yet");
    expect(screen.getByTestId("knowledge-vocabulary-v3-used-by")).toHaveTextContent("no connector built on it yet");
  });

  it("shows loading states", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true } as unknown as ReturnType<typeof useSWR>);
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-vocabulary-loading")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-learned-loading")).toBeInTheDocument();
  });

  it("lists every human decision separately from learned knowledge (REQ-049 item 9)", () => {
    mockBoth(CAPABILITIES, {
      ...KNOWLEDGE,
      decisions: [
        {
          id: "ap-9", at: "2026-10-30T10:00:00Z", actor: "alice", kind: "approve",
          business_decision: true, learned: false, connector_id: "c-h", connector_name: "HarborPMS",
          repair_run_id: "rep-9", from_release_id: "r1", to_release_id: "r2",
          summary: "Business decision: accept net totals going forward",
        },
        {
          id: "ap-8", at: "2026-10-30T09:00:00Z", actor: "alice", kind: "approve",
          business_decision: false, learned: true, connector_id: "c1", connector_name: "AtlasPMS",
          repair_run_id: "rep-8", from_release_id: "r1", to_release_id: "r2",
          summary: "Approved page_to_cursor → r2 (learned)",
        },
        {
          id: "ap-7", at: "2026-10-30T08:00:00Z", actor: "alice", kind: "reject",
          business_decision: false, learned: false, connector_id: "c-h", connector_name: "HarborPMS",
          repair_run_id: "rep-7", from_release_id: null, to_release_id: null,
          summary: "Denied: kept everything as-is, contact the vendor",
        },
      ],
    });
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-decisions-title")).toHaveTextContent("Decisions");
    expect(screen.getByTestId("knowledge-decision-0")).toHaveTextContent("Business decision");
    expect(screen.getByTestId("knowledge-decision-0")).toHaveTextContent("accept net totals going forward");
    expect(screen.getByTestId("knowledge-decision-0")).toHaveTextContent("HarborPMS");
    expect(screen.getByTestId("knowledge-decision-0")).toHaveTextContent("alice");
    expect(screen.getByTestId("knowledge-decision-0").querySelector('a[href="/repairs/rep-9"]')).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-decision-1")).toHaveTextContent("Approved (learned)");
    expect(screen.getByTestId("knowledge-decision-1")).toHaveTextContent("page_to_cursor");
    expect(screen.getByTestId("knowledge-decision-2")).toHaveTextContent("Denied");
    // Learned entries stay their own section.
    expect(screen.getByTestId("knowledge-entry-0")).toBeInTheDocument();
  });

  it("shows the empty decisions state when nobody decided yet", () => {
    mockBoth(CAPABILITIES, { ...KNOWLEDGE, decisions: [] });
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-decisions-empty")).toBeInTheDocument();
  });
});
