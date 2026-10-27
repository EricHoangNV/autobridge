import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HypothesesPanel } from "./HypothesesPanel";

describe("HypothesesPanel", () => {
  it("shows empty when no hypotheses", () => {
    render(<HypothesesPanel hypothesisIds={[]} />);
    expect(screen.getByTestId("hypotheses-empty")).toBeInTheDocument();
  });

  it("renders hypothesis ids as fallback", () => {
    render(<HypothesesPanel hypothesisIds={["hyp-1", "hyp-2"]} />);
    expect(screen.getByTestId("hypotheses-panel")).toBeInTheDocument();
    expect(screen.getByTestId("hypothesis-hyp-1")).toBeInTheDocument();
    expect(screen.getByTestId("hypothesis-hyp-2")).toBeInTheDocument();
  });

  it("renders hypothesis cards with family and claim", () => {
    render(
      <HypothesesPanel
        hypothesisIds={["h1"]}
        hypotheses={[{ id: "h1", family: "date", claim: "Use arrival_date", strategy: "local", prior: 0.7 }]}
      />,
    );
    expect(screen.getByTestId("hypothesis-h1")).toBeInTheDocument();
    expect(screen.getByTestId("hypothesis-family-h1")).toHaveTextContent("date");
    expect(screen.getByTestId("hypothesis-claim-h1")).toHaveTextContent("Use arrival_date");
  });

  it("renders probes section when probes provided", () => {
    render(
      <HypothesesPanel
        hypothesisIds={["h1"]}
        hypotheses={[{ id: "h1" }]}
        probes={[{ id: "probe-1", kind: "probe", payload: { url: "/test" }, parent_ids: [], run_id: "run-1", created_at: new Date().toISOString() }]}
      />,
    );
    expect(screen.getByTestId("probes-section")).toBeInTheDocument();
    expect(screen.getByTestId("probe-probe-1")).toBeInTheDocument();
  });
});
