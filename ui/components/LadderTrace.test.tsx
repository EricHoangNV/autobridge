import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LadderTrace } from "./LadderTrace";

describe("LadderTrace", () => {
  it("renders ladder trace with level badge", () => {
    render(<LadderTrace ladderLevelReached={1} ruleId="rename_field" decision="propose" />);
    expect(screen.getByTestId("ladder-trace")).toBeInTheDocument();
    expect(screen.getByTestId("ladder-level-badge")).toBeInTheDocument();
    expect(screen.getByTestId("ladder-level-badge").getAttribute("data-level")).toBe("1");
    expect(screen.getByTestId("ladder-level-badge").textContent).toContain("L1");
  });

  it("shows L0 badge when level is 0", () => {
    render(<LadderTrace ladderLevelReached={0} decision="propose" />);
    expect(screen.getByTestId("ladder-level-badge").textContent).toContain("L0");
  });

  it("shows L4 for escalation", () => {
    render(<LadderTrace ladderLevelReached={4} decision="escalate" escalationBundle={{ reason: "CAPABILITY_REMOVED", question: "What should be done?" }} />);
    expect(screen.getByTestId("ladder-level-badge").textContent).toContain("L4");
    expect(screen.getByTestId("ladder-escalation-question")).toBeInTheDocument();
  });

  it("shows rule id when provided", () => {
    render(<LadderTrace ladderLevelReached={1} ruleId="offset_to_cursor" decision="propose" />);
    expect(screen.getByTestId("ladder-rule-id")).toHaveTextContent("offset_to_cursor");
  });

  it("shows decision badge", () => {
    render(<LadderTrace ladderLevelReached={1} decision="escalate" />);
    expect(screen.getByTestId("ladder-decision")).toHaveTextContent("escalate");
  });

  it("renders ladder steps", () => {
    render(<LadderTrace ladderLevelReached={1} ruleId="rename_field" decision="propose" />);
    expect(screen.getByTestId("ladder-step-0")).toBeInTheDocument();
  });

  it("renders explicit trace when provided", () => {
    const trace = [
      { level: 0, action: "knowledge_miss", reason: "L0 miss" },
      { level: 1, action: "propose", reason: "L1 rule matched", rule_id: "rename_field" },
    ];
    render(<LadderTrace ladderLevelReached={1} ruleId="rename_field" decision="propose" trace={trace} />);
    expect(screen.getByTestId("ladder-step-0")).toBeInTheDocument();
    expect(screen.getByTestId("ladder-step-1")).toBeInTheDocument();
    expect(screen.getByTestId("ladder-step-badge-0")).toBeInTheDocument();
  });

  it("renders legend with all levels L0-L4", () => {
    render(<LadderTrace ladderLevelReached={1} decision="propose" />);
    for (let l = 0; l <= 4; l++) {
      expect(screen.getByTestId(`ladder-legend-L${l}`)).toBeInTheDocument();
    }
    // L1 should be marked as reached
    expect(screen.getByTestId("ladder-legend-L1").getAttribute("data-reached")).toBe("true");
    expect(screen.getByTestId("ladder-legend-L0").getAttribute("data-reached")).toBe("false");
  });
});
