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

describe("VerifierVerdict REQ-047", () => {
  it("says no patch to verify when the model escalated", () => {
    render(
      <VerifierVerdict
        decision="escalate"
        modelEscalated
        escalationBundle={{ reason: "gross_to_net: no fix; model rationale: business call", question: "Should total_amount track gross or net?", evidence_ids: [] }}
      />,
    );
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("not_verified");
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("no patch to verify (the model escalated)");
  });

  it("keeps the generic escalated wording without a model escalation", () => {
    render(
      <VerifierVerdict
        decision="escalate"
        escalationBundle={{ reason: "no fix", question: "Which mapping?", evidence_ids: [] }}
      />,
    );
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("no verifier report — escalated");
  });
});
