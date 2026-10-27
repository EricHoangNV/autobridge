import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HealthBadge } from "./HealthBadge";

describe("HealthBadge", () => {
  it("renders status", () => {
    render(<HealthBadge status="healthy" />);
    expect(screen.getByTestId("health-badge")).toHaveTextContent("healthy");
  });

  it("handles degraded and blocked", () => {
    const { rerender } = render(<HealthBadge status="degraded" />);
    expect(screen.getByTestId("health-badge")).toHaveTextContent("degraded");
    rerender(<HealthBadge status="blocked" />);
    expect(screen.getByTestId("health-badge")).toHaveTextContent("blocked");
  });
});
