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
    v3: { methods: ["EVENT"], auth_types: ["edge_relay"], pagination_strategies: [], protocols: ["fias_tcp"], joins: true, body_templates: true, event_feeds: true, command_templates: true },
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
      learned_from: { run_id: "r1", rung: 1, tokens: 0, cost: "0", at: "2026-01-01T00:00:00Z" },
      times_reused: 1,
      tokens_saved: 0,
      cost_saved: "0",
      approvals: 1,
    },
  ],
  connectors: [
    { connector_id: "c1", name: "Atlas", vocabulary: "v1", builds: 1, confirmed_hypotheses: ["pagination:/reservations:cursor"], last_built_at: null },
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

  it("shows the empty state when nothing is learned", () => {
    mockBoth(CAPABILITIES, { entries: [], connectors: [], counts: {} });
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-learned-empty")).toBeInTheDocument();
  });

  it("shows loading states", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true } as unknown as ReturnType<typeof useSWR>);
    render(<KnowledgePage />);
    expect(screen.getByTestId("knowledge-vocabulary-loading")).toBeInTheDocument();
    expect(screen.getByTestId("knowledge-learned-loading")).toBeInTheDocument();
  });
});
