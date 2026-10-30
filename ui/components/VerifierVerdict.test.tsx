import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerifierVerdict, displayBundleReason } from "./VerifierVerdict";

describe("VerifierVerdict", () => {
  it("shows pass when report exists and propose", () => {
    render(<VerifierVerdict verifierReportId="rep-123" decision="propose" />);
    expect(screen.getByTestId("verifier-verdict")).toBeInTheDocument();
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("pass");
  });

  it("shows escalation bundle with reason and action but no repeated question (REQ-048)", () => {
    render(
      <VerifierVerdict
        decision="escalate"
        escalationBundle={{ reason: "CAPABILITY_REMOVED", question: "Which endpoint should replace /old?", suggested_action: "pick one", evidence_ids: ["ev-1"] }}
      />,
    );
    expect(screen.getByTestId("escalation-bundle")).toBeInTheDocument();
    expect(screen.getByTestId("escalation-reason")).toHaveTextContent("CAPABILITY_REMOVED");
    // The question lives once in the top decision box — never in the bundle.
    expect(screen.queryByTestId("escalation-question")).not.toBeInTheDocument();
    expect(screen.getByTestId("escalation-bundle")).toHaveTextContent("pick one");
    expect(screen.getByTestId("escalation-bundle")).not.toHaveTextContent("Which endpoint");
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

describe("displayBundleReason (REQ-048)", () => {
  it("strips the trailing model-rationale clause", () => {
    expect(
      displayBundleReason("gross_to_net: semantic drift, no verified fix; model rationale: Net versus gross is a business call."),
    ).toBe("gross_to_net: semantic drift, no verified fix");
  });

  it("passes reasons without the clause through", () => {
    expect(displayBundleReason("CAPABILITY_REMOVED")).toBe("CAPABILITY_REMOVED");
    expect(displayBundleReason(null)).toBe("");
  });
});
