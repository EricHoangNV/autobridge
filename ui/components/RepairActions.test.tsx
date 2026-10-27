import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RepairActions } from "./RepairActions";

describe("RepairActions", () => {
  it("renders Approve/Reject/Rollback actions", () => {
    render(<RepairActions repairId="rep-1" decision="propose" />);
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("repair-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("repair-reject-btn")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-btn")).toBeInTheDocument();
    expect(screen.getByTestId("repair-actor-input")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-target-input")).toBeInTheDocument();
  });

  it("shows disabled when not propose", () => {
    render(<RepairActions repairId="rep-1" decision="escalate" />);
    expect(screen.getByText(/Approve\/Reject available only when decision is/)).toBeInTheDocument();
  });
});
