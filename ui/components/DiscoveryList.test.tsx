import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DiscoveryList } from "./DiscoveryList";
import type { DiscoveryRecordOut } from "@/lib/api/client";

const record = (overrides: Partial<DiscoveryRecordOut> = {}): DiscoveryRecordOut => ({
  id: "row-1",
  discovery_id: "disc-test-001",
  vendor: "HarborPMS",
  base_url: "https://harbor.example.com",
  candidate_entities: ["Reservation"],
  evidence_refs: [],
  status: "discovered",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...overrides,
});

describe("DiscoveryList", () => {
  it("shows empty state when nothing is imported", () => {
    render(<DiscoveryList records={[]} />);
    expect(screen.getByTestId("discovery-empty")).toBeTruthy();
  });

  it("shows all five lifecycle states from the static fixture", () => {
    const records = [
      record({ discovery_id: "a", vendor: "AtlasPMS", status: "discovered" }),
      record({ discovery_id: "b", vendor: "HarborPMS", status: "needs_confirmation" }),
      record({ discovery_id: "c", vendor: "LegacyStay", status: "authorized" }),
      record({ discovery_id: "d", vendor: "FIAS PABX", status: "connected" }),
      record({ discovery_id: "e", vendor: "HealPMS", status: "healing" }),
    ];
    render(<DiscoveryList records={records} />);
    const statuses = screen.getAllByTestId("discovery-status").map((el) => el.textContent);
    expect(statuses).toEqual([
      "discovered",
      "needs confirmation",
      "authorized",
      "connected",
      "healing",
    ]);
  });
});
