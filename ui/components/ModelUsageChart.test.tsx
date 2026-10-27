import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ModelUsageChart } from "./ModelUsageChart";

const usage = {
  total_calls: 4,
  total_input_tokens: 1000,
  total_output_tokens: 500,
  total_cost: "0.01",
  frontier_tokens: 900,
  small_tokens: 600,
  by_tier: { frontier: 900, small: 600 },
  by_provider: { anthropic: 3, openai: 1 },
  entries: [
    {
      provider: "anthropic",
      model: "claude-haiku",
      tier: "small",
      total_calls: 3,
      total_input_tokens: 400,
      total_output_tokens: 200,
      total_cost: "0.005",
      by_run: [
        { run_id: "run-a", purpose: "rank", tokens: 400 },
        { run_id: "run-b", purpose: "rank", tokens: 200 },
      ],
    },
    {
      provider: "anthropic",
      model: "claude-opus",
      tier: "frontier",
      total_calls: 1,
      total_input_tokens: 600,
      total_output_tokens: 300,
      total_cost: "0.005",
      by_run: [{ run_id: "run-a", purpose: "patch", tokens: 900 }],
    },
  ],
};

describe("ModelUsageChart", () => {
  it("renders empty state without usage", () => {
    render(<ModelUsageChart usage={null} />);
    expect(screen.getByTestId("model-usage-chart-empty")).toBeInTheDocument();
  });

  it("renders empty state without entries", () => {
    render(<ModelUsageChart usage={{ ...usage, entries: [] }} />);
    expect(screen.getByTestId("model-usage-chart-empty")).toBeInTheDocument();
  });

  it("renders one row per run with stacked frontier/small segments", () => {
    const { container } = render(<ModelUsageChart usage={usage} />);
    expect(screen.getByTestId("model-usage-chart")).toBeInTheDocument();
    expect(screen.getAllByTestId("model-usage-chart-row")).toHaveLength(2);
    // run-a has both tiers; run-b small only
    expect(container.querySelectorAll('[data-testid="model-usage-chart-row-frontier"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="model-usage-chart-row-small"]')).toHaveLength(2);
  });

  it("sorts highest-token run first", () => {
    render(<ModelUsageChart usage={usage} />);
    const rows = screen.getAllByTestId("model-usage-chart-row");
    expect(rows[0].getAttribute("data-run-id")).toBe("run-a");
    expect(rows[1].getAttribute("data-run-id")).toBe("run-b");
  });

  it("highlights related run ids", () => {
    render(<ModelUsageChart usage={usage} highlightRunIds={["run-b"]} />);
    const rows = screen.getAllByTestId("model-usage-chart-row");
    expect(rows[0].getAttribute("data-highlight")).toBe("false");
    expect(rows[1].getAttribute("data-highlight")).toBe("true");
  });

  it("caps rows at maxRuns and reports the remainder", () => {
    render(<ModelUsageChart usage={usage} maxRuns={1} />);
    expect(screen.getAllByTestId("model-usage-chart-row")).toHaveLength(1);
    expect(screen.getByTestId("model-usage-chart-more")).toHaveTextContent("+ 1 more run");
  });

  it("renders loading state", () => {
    render(<ModelUsageChart usage={null} loading />);
    expect(screen.getByTestId("model-usage-chart-loading")).toBeInTheDocument();
  });
});
