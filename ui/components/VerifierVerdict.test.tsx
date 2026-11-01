import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { VerifierVerdict, displayBundleReason } from "./VerifierVerdict";

describe("VerifierVerdict", () => {
  it("shows pass when report exists and propose", () => {
    render(<VerifierVerdict verifierReportId="rep-123" decision="propose" />);
    expect(screen.getByTestId("verifier-verdict")).toBeInTheDocument();
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("pass");
  });

  it("shows pass for an approved verified fix, never fail (REQ-073 item 2)", () => {
    render(
      <VerifierVerdict
        verifierReportId="rep-123"
        decision="approved"
        visibleTestResults={[{ name: "money_invariant", passed: true }]}
      />,
    );
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("pass");
    expect(screen.getByTestId("verifier-verdict")).not.toHaveTextContent("fail");
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("1/1");
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("checks pass on this patch");
  });

  it("an approved report with no visible checks still reads pass, never fail (REQ-073 item 2)", () => {
    render(<VerifierVerdict verifierReportId="ee21f3a2-0000" decision="approved" visibleTestResults={[]} />);
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("pass");
    expect(screen.getByTestId("verifier-verdict")).not.toHaveTextContent("fail");
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("passed on this patch");
  });

  it("a report with a failing visible check reads fail honestly (REQ-073 item 2)", () => {
    render(
      <VerifierVerdict
        verifierReportId="rep-123"
        decision="propose"
        visibleTestResults={[{ name: "a", passed: true }, { name: "b", passed: false }]}
      />,
    );
    expect(screen.getByTestId("verifier-verdict-badge").getAttribute("data-verdict")).toBe("fail");
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("1/2");
    expect(screen.getByTestId("verifier-verdict")).toHaveTextContent("checks fail on this patch");
  });

  it("shows one verdict label, no duplicate heading (REQ-073 item 2)", () => {
    render(<VerifierVerdict verifierReportId="rep-123" decision="propose" />);
    expect(screen.queryByText("Verifier, hidden fixtures")).not.toBeInTheDocument();
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
