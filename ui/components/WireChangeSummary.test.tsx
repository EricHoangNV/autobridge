import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { summarizeWireChange } from "./WireChangeSummary";
import { WireChangeSummary } from "./WireChangeSummary";

describe("summarizeWireChange", () => {
  it("describes a pagination switch before → after", () => {
    const lines = summarizeWireChange("PAGINATION_CHANGED", {
      pagination_changed: true,
      pagination_details: {
        list_reservations: { previous: { strategy: "page" }, current: { strategy: "cursor" } },
      },
    });
    expect(lines.join("\n")).toContain("pagination on list_reservations: page → cursor");
  });

  it("describes a single field rename", () => {
    const lines = summarizeWireChange("FIELD_RENAME", {
      keys_changed: true,
      changed_entities: ["Reservation"],
      keys_details: { Reservation: { previous: ["guest_id", "name"], current: ["guest_ref", "name"] } },
    });
    expect(lines.join("\n")).toContain("field `guest_id` renamed `guest_ref` on Reservation");
  });

  it("describes type changes per field", () => {
    const lines = summarizeWireChange("SEMANTIC_VALUE_DRIFT", {
      types_changed: true,
      types_details: { FolioCharge: { previous: { amount: "gross" }, current: { amount: "net" } } },
    });
    expect(lines.join("\n")).toContain("field `amount` on FolioCharge: gross → net");
  });

  it("is honest when nothing was recorded", () => {
    expect(summarizeWireChange("UNKNOWN", null)).toEqual([
      "No wire-shape change was recorded for this drift.",
    ]);
    const lines = summarizeWireChange("RATE_LIMIT", {});
    expect(lines.join("\n").toLowerCase()).toContain("no wire-shape change was recorded");
  });
});

describe("WireChangeSummary", () => {
  it("renders one line per change", () => {
    render(
      <WireChangeSummary
        driftClass="PAGINATION_CHANGED"
        delta={{ pagination_details: { list: { previous: { strategy: "page" }, current: { strategy: "cursor" } } } }}
      />,
    );
    expect(screen.getByTestId("wire-change-summary")).toBeInTheDocument();
    expect(screen.getByTestId("wire-change-line-0")).toHaveTextContent("page → cursor");
  });
});
