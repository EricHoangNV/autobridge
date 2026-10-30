import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("renders empty state", () => {
    render(<Sparkline values={[]} />);
    expect(screen.getByTestId("sparkline")).toHaveTextContent("no data");
  });

  it("renders svg with bars", () => {
    const { container } = render(<Sparkline values={[1, 2, 3, 2]} />);
    expect(screen.getByTestId("sparkline").tagName.toLowerCase()).toBe("svg");
    expect(container.querySelectorAll('[data-testid="sparkline-bar"]').length).toBe(4);
  });

  it("draws zero values as baseline ticks", () => {
    const { container } = render(<Sparkline values={[0, 5, 0]} />);
    expect(container.querySelectorAll('[data-testid="sparkline-tick"]').length).toBe(2);
    expect(container.querySelectorAll('[data-testid="sparkline-bar"]').length).toBe(1);
  });

  it("single value still renders", () => {
    render(<Sparkline values={[5]} />);
    expect(screen.getByTestId("sparkline")).toBeInTheDocument();
  });

  it("leaves headroom so no bar fills the cell height (REQ-048)", () => {
    const { container } = render(<Sparkline values={[5, 5]} height={40} />);
    const bars = container.querySelectorAll('[data-testid="sparkline-bar"]');
    expect(bars.length).toBe(2);
    for (const b of Array.from(bars)) {
      expect(Number(b.getAttribute("height"))).toBeLessThan(40);
    }
  });
});
