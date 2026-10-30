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

  it("escalation with an empty proposal patch keeps plain Reject (REQ-049)", () => {
    mockOperator("alice");
    render(<RepairActions repairId="rep-1" decision="escalate" proposal={{ label: "Accept net", effect: "x", patch: [] }} />);
    expect(screen.queryByTestId("repair-approve-proposal-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repair-deny-btn")).not.toBeInTheDocument();
    expect(screen.getByTestId("repair-reject-btn")).toBeInTheDocument();
  });
});
