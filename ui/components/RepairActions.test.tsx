import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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
    render(<RepairActions repairId="rep-1" decision="propose" releases={[{ id: "rel-1", version: 1 }]} />);
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("repair-approve-btn")).toBeInTheDocument();
    expect(screen.getByTestId("repair-reject-btn")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-btn")).toBeInTheDocument();
    expect(screen.getByTestId("rollback-target-select")).toBeInTheDocument();
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

  it("decided repairs show the outcome with actions disabled (REQ-053 item 4)", () => {
    mockOperator("alice");
    // REQ-073 item 5: an approved repair shows the outcome only — no
    // Reject patch, no Roll back controls (they live on the connector page).
    const { rerender } = render(<RepairActions repairId="rep-1" decision="approved" />);
    const approved = screen.getByTestId("repair-approve-btn");
    expect(approved).toBeDisabled();
    expect(approved).toHaveTextContent("Approved ✓");
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rollback-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rollback-target-select")).not.toBeInTheDocument();
    rerender(<RepairActions repairId="rep-1" decision="reject" />);
    const rejected = screen.getByTestId("repair-approve-btn");
    expect(rejected).toBeDisabled();
    expect(rejected).toHaveTextContent("Rejected");
    rerender(<RepairActions repairId="rep-1" decision="rolled_back" />);
    const rolledBack = screen.getByTestId("repair-approve-btn");
    expect(rolledBack).toBeDisabled();
    expect(rolledBack).toHaveTextContent("Rolled back");
  });

  it("escalations show no Approve button and an enabled Deny (REQ-048, REQ-054)", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="escalate" releases={[{ id: "rel-1", version: 1 }]} />);
    expect(screen.queryByTestId("repair-approve-btn")).not.toBeInTheDocument();
    expect(screen.queryByText(/Approve\/Reject available only when decision is/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    const deny = screen.getByTestId("repair-deny-btn");
    expect(deny).toHaveTextContent(/Deny: keep everything as-is/);
    expect(deny).not.toBeDisabled();
    expect(screen.getByTestId("rollback-btn")).toBeInTheDocument();
  });

  it("shows no developer text outside propose/escalate (REQ-073 item 6)", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="climbing" />);
    expect(screen.queryByText(/Approve\/Reject available only when decision is/)).not.toBeInTheDocument();
  });

  it("the rollback control picks a release from a list, never a typed id (REQ-073 item 6)", () => {
    mockOperator("alice");
    const { rerender } = render(<RepairActions repairId="rep-1" decision="propose" releases={[{ id: "rel-1", version: 1 }, { id: "rel-2", version: 2 }]} />);
    const select = screen.getByTestId("rollback-target-select");
    expect(select).toBeInTheDocument();
    expect(screen.getByText("r1")).toBeInTheDocument();
    expect(screen.getByText("r2")).toBeInTheDocument();
    // No releases given: no rollback control at all.
    rerender(<RepairActions repairId="rep-1" decision="propose" />);
    expect(screen.queryByTestId("rollback-target-select")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rollback-btn")).not.toBeInTheDocument();
  });

  it("hides the action until a patch has passed", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="propose" patchReady={false} />);
    const approve = screen.getByTestId("repair-approve-btn");
    expect(approve).toBeDisabled();
    expect(approve).toHaveTextContent("No patch to approve yet");
  });

  it("enables the action once the patch is ready", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="propose" patchReady />);
    expect(screen.getByTestId("repair-approve-btn")).not.toBeDisabled();
  });

  it("escalation with a model proposal shows Approve with the model label plus Deny (REQ-049)", async () => {
    const approveFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "ap-1", to_release_id: "rel-2" }) });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, opts?: { method?: string }) => {
        if (typeof url === "string" && url.includes("/operator")) {
          return Promise.resolve({ ok: true, json: async () => ({ name: "alice" }) });
        }
        return approveFetch(url, opts);
      }),
    );
    render(
      <RepairActions
        repairId="rep-9"
        decision="escalate"
        proposal={{ label: "Accept net", effect: "Retarget the totals check.", patch: [{ op: "replace", path: "/entities/Reservation/checks/2/compare_to", value: "$.total" }] }}
      />,
    );
    const approve = screen.getByTestId("repair-approve-proposal-btn");
    expect(approve).toHaveTextContent("Approve: Accept net");
    expect(approve).not.toBeDisabled();
    const deny = screen.getByTestId("repair-deny-btn");
    expect(deny).toHaveTextContent(/Deny: keep everything as-is/);
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-approve-btn")).not.toBeInTheDocument();
    fireEvent.click(approve);
    expect(approveFetch).toHaveBeenCalledWith(
      expect.stringContaining("/repairs/rep-9/approve-proposal"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(await screen.findByTestId("repair-action-success")).toHaveTextContent(/business decision/);
  });

  it("escalation with a proposal denies via reject and keeps the error (REQ-049)", async () => {
    const denyFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "ap-2", decision: "reject" }) });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, opts?: { method?: string }) => {
        if (typeof url === "string" && url.includes("/operator")) {
          return Promise.resolve({ ok: true, json: async () => ({ name: "alice" }) });
        }
        return denyFetch(url, opts);
      }),
    );
    render(
      <RepairActions
        repairId="rep-9"
        decision="escalate"
        proposal={{ label: "Accept net", effect: "Retarget.", patch: [{ op: "replace", path: "/a", value: 1 }] }}
      />,
    );
    fireEvent.click(screen.getByTestId("repair-deny-btn"));
    expect(denyFetch).toHaveBeenCalledWith(
      expect.stringContaining("/repairs/rep-9/reject"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(await screen.findByTestId("repair-action-success")).toHaveTextContent(/kept everything as-is/);
  });

  it("escalation with an empty proposal patch keeps Deny, no Approve (REQ-049, REQ-054)", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="escalate" proposal={{ label: "Accept net", effect: "x", patch: [] }} />);
    expect(screen.queryByTestId("repair-approve-proposal-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-reject-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("repair-deny-btn")).toBeInTheDocument();
  });
});
