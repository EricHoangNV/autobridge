import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SyncOutcomeBadge } from "./SyncOutcomeBadge";

describe("SyncOutcomeBadge", () => {
  it("renders Complete/Degraded/Blocked", () => {
    const { rerender } = render(<SyncOutcomeBadge outcome="Complete" />);
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("Complete");
    rerender(<SyncOutcomeBadge outcome="Degraded" />);
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("Degraded");
    rerender(<SyncOutcomeBadge outcome="Blocked" />);
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("Blocked");
  });

  it("renders dash when null", () => {
    render(<SyncOutcomeBadge outcome={null} />);
    expect(screen.getByTestId("sync-outcome-badge")).toHaveTextContent("—");
  });
});
