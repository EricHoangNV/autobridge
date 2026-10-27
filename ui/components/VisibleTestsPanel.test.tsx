import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { VisibleTestsPanel } from "./VisibleTestsPanel";

describe("VisibleTestsPanel", () => {
  it("shows empty when no tests", () => {
    render(<VisibleTestsPanel visibleTestResults={[]} />);
    expect(screen.getByTestId("visible-tests-empty")).toBeInTheDocument();
  });

  it("shows empty when null", () => {
    render(<VisibleTestsPanel visibleTestResults={null} />);
    expect(screen.getByTestId("visible-tests-empty")).toBeInTheDocument();
  });

  it("renders pass/fail tests", () => {
    render(
      <VisibleTestsPanel
        visibleTestResults={[
          { name: "records_match", passed: true },
          { name: "money_exact", status: "fail", message: "mismatch" },
        ]}
      />,
    );
    expect(screen.getByTestId("visible-tests-panel")).toBeInTheDocument();
    expect(screen.getByTestId("visible-test-0")).toBeInTheDocument();
    expect(screen.getByTestId("visible-test-1")).toBeInTheDocument();
    expect(screen.getByTestId("visible-test-name-0")).toHaveTextContent("records_match");
    expect(screen.getByTestId("visible-tests-passed-count")).toHaveTextContent("1 passed");
    expect(screen.getByTestId("visible-tests-failed-count")).toHaveTextContent("1 failed");
  });
});
