import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ModelUsageView } from "./ModelUsageView";

describe("ModelUsageView", () => {
  it("shows empty when no usage", () => {
    render(<ModelUsageView modelUsage={null} />);
    expect(screen.getByTestId("model-usage-empty")).toBeInTheDocument();
  });

  it("shows empty when empty object", () => {
    render(<ModelUsageView modelUsage={{}} />);
    expect(screen.getByTestId("model-usage-empty")).toBeInTheDocument();
  });

  it("renders frontier/small/calls counts", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 100, small_tokens: 50, calls: 2 }} />);
    expect(screen.getByTestId("model-usage-view")).toBeInTheDocument();
    expect(screen.getByTestId("model-usage-frontier-tokens")).toHaveTextContent("100");
    expect(screen.getByTestId("model-usage-small-tokens")).toHaveTextContent("50");
    expect(screen.getByTestId("model-usage-calls")).toHaveTextContent("2");
  });

  it("shows zero-frontier badge when frontier is 0", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 0, small_tokens: 10, calls: 1 }} />);
    expect(screen.getByTestId("model-usage-zero-frontier")).toBeInTheDocument();
    expect(screen.getByTestId("model-usage-zero-frontier")).toHaveTextContent("deterministic repair");
  });

  it("does not show zero-frontier badge when frontier > 0", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 10, small_tokens: 5, calls: 1 }} />);
    expect(screen.queryByTestId("model-usage-zero-frontier")).not.toBeInTheDocument();
  });

  it("compact mode hides detail rows", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 5, small_tokens: 5, calls: 1 }} compact />);
    expect(screen.getByTestId("model-usage-view")).toBeInTheDocument();
    expect(screen.queryByTestId("model-usage-raw-toggle")).not.toBeInTheDocument();
  });

  it("shows cost when present", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 10, small_tokens: 0, calls: 1, total_cost: "0.002" }} />);
    expect(screen.getByTestId("model-usage-cost")).toHaveTextContent("0.002");
  });

  it("hides the zero badge and shows the live counter while pending", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 0, small_tokens: 10, calls: 1 }} pending liveFrontierTokens={0} />);
    expect(screen.queryByTestId("model-usage-zero-frontier")).not.toBeInTheDocument();
    expect(screen.getByTestId("model-usage-frontier-tokens")).toHaveTextContent("0");
  });

  it("shows the live tick value instead of the final total", () => {
    render(<ModelUsageView modelUsage={{ frontier_tokens: 8412, small_tokens: 0, calls: 9 }} liveFrontierTokens={1200} />);
    expect(screen.getByTestId("model-usage-frontier-tokens")).toHaveTextContent("1,200");
  });

  it("shows the budget that applies, and no budget clause without one (REQ-048)", () => {
    const { rerender } = render(
      <ModelUsageView modelUsage={{ frontier_tokens: 3530, small_tokens: 0, calls: 1 }} budget={131072} />,
    );
    expect(screen.getByTestId("model-usage-view")).toHaveTextContent("of a 131,072 budget");
    rerender(<ModelUsageView modelUsage={{ frontier_tokens: 3530, small_tokens: 0, calls: 1 }} />);
    expect(screen.getByTestId("model-usage-view")).not.toHaveTextContent("budget");
  });
});
