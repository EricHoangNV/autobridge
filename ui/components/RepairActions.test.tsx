import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RepairActions } from "./RepairActions";

function mockOperator(name: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({ name }) }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RepairActions", () => {
  it("renders Approve/Reject/Rollback actions", async () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="propose" />);
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("repair-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("repair-reject-btn")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-btn")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-target-input")).toBeInTheDocument();
    expect(await screen.findByTestId("approving-as")).toHaveTextContent("Approving as alice");
  });

  it("falls back to the default operator when the lookup fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("connection refused")),
    );
    render(<RepairActions repairId="rep-1" decision="propose" />);
    expect(await screen.findByTestId("approving-as")).toHaveTextContent("Approving as operator");
  });

  it("escalations show no Approve button and an enabled Reject (REQ-048)", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="escalate" />);
    expect(screen.queryByTestId("repair-approve-btn")).not.toBeInTheDocument();
    expect(screen.queryByText(/Approve\/Reject available only when decision is/)).not.toBeInTheDocument();
    expect(screen.getByTestId("repair-reject-btn")).toBeInTheDocument();
    expect(screen.getByTestId("repair-reject-btn")).not.toBeDisabled();
    expect(screen.getByTestId("rollback-btn")).toBeInTheDocument();
  });

  it("shows disabled when not propose and not escalate", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="climbing" />);
    expect(screen.getByText(/Approve\/Reject available only when decision is/)).toBeInTheDocument();
  });

  it("hides the action until a patch has passed", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="propose" patchReady={false} />);
    const approve = screen.getByTestId("repair-approve-btn");
    expect(approve).toBeDisabled();
    expect(approve).toHaveTextContent("No patch to approve yet");
    expect(screen.getByText(/has not passed a patch yet/)).toBeInTheDocument();
  });

  it("enables the action once the patch is ready", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="propose" patchReady />);
    expect(screen.getByTestId("repair-approve-btn")).not.toBeDisabled();
    expect(screen.queryByText(/has not passed a patch yet/)).not.toBeInTheDocument();
  });
});
