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

  it("shows disabled when not propose", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="escalate" />);
    expect(screen.getByText(/Approve\/Reject available only when decision is/)).toBeInTheDocument();
  });
});
