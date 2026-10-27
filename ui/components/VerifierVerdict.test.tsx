import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerifierVerdict } from "./VerifierVerdict";

describe("VerifierVerdict", () => {
  it("shows pass when report exists and propose", () => {
    render(<VerifierVerdict verifierReportId="rep-123" decision="propose" />);
    expect(screen.getByTestId("verifier-verdict")).toBeInTheDocument();
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("pass");
  });

  it("shows escalation bundle when present", () => {
    render(
      <VerifierVerdict
        decision="escalate"
        escalationBundle={{ reason: "CAPABILITY_REMOVED", question: "Which endpoint should replace /old?", evidence_ids: ["ev-1"] }}
      />,
    );
    expect(screen.getByTestId("escalation-bundle")).toBeInTheDocument();
    expect(screen.getByTestId("escalation-reason")).toHaveTextContent("CAPABILITY_REMOVED");
    expect(screen.getByTestId("escalation-question")).toHaveTextContent("Which endpoint");
  });

  it("shows report id", () => {
    render(<VerifierVerdict verifierReportId="report-abc-123-def-456" decision="propose" />);
    expect(screen.getByTestId("verifier-report-id")).toBeInTheDocument();
  });
});
