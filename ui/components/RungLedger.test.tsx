import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RungLedger } from "./RungLedger";

const LEDGER = {
  repair_run_id: "repair-1",
  decision: "escalate",
  resolved_rung: 4,
  attempts: [
    { rung: 0, action: "knowledge_miss", reason: "L0 miss: no known fix for atlas+page_to_cursor", calls: [] },
    { rung: 1, action: "rule_applied", reason: "L1: page_to_cursor precondition matched", calls: [] },
    {
      rung: 3,
      action: "frontier_generate",
      reason: "L3 invoked: enumerator yielded 1 < 2",
      calls: [{ tier: "frontier", purpose: "l3-generate-hypothesis", input_tokens: 100, output_tokens: 50, cost: "0.000675" }],
    },
    { rung: 4, action: "escalate", reason: "Budget exhausted", calls: [] },
  ],
  total_calls: 1,
  small_tokens: 0,
  frontier_tokens: 150,
  total_cost: "0.000675",
};

describe("RungLedger", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => LEDGER }) as Response),
    );
  });

  it("renders every rung with its reason and linked model calls", async () => {
    render(<RungLedger repairId="repair-1" />);
    await waitFor(() => expect(screen.getByTestId("rung-ledger")).toBeInTheDocument());
    expect(screen.getByTestId("rung-attempt-L0").textContent).toContain("L0 miss");
    expect(screen.getByTestId("rung-attempt-L0").textContent).toContain("0 model calls");
    expect(screen.getByTestId("rung-calls-L3").textContent).toContain("l3-generate-hypothesis");
    expect(screen.getByTestId("rung-calls-L3").textContent).toContain("150");
    expect(screen.getByTestId("rung-ledger-totals").textContent).toContain("resolved at L4");
    expect(screen.getByTestId("rung-ledger-totals").textContent).toContain("150 frontier tokens");
  });

  it("shows the empty state when no rungs were recorded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ ...LEDGER, attempts: [] }) }) as Response),
    );
    render(<RungLedger repairId="repair-1" />);
    await waitFor(() => expect(screen.getByTestId("rung-ledger-empty")).toBeInTheDocument());
  });

  it("shows the error state when the ledger endpoint fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500 }) as Response));
    render(<RungLedger repairId="repair-1" />);
    await waitFor(() => expect(screen.getByTestId("rung-ledger-error")).toBeInTheDocument());
  });
});
