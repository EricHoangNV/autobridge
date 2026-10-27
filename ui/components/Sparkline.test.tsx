import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("renders empty state", () => {
    render(<Sparkline values={[]} />);
    expect(screen.getByTestId("sparkline")).toHaveTextContent("no data");
  });

  it("renders svg with polyline", () => {
    const { container } = render(<Sparkline values={[1, 2, 3, 2]} />);
    expect(screen.getByTestId("sparkline").tagName.toLowerCase()).toBe("svg");
    expect(container.querySelector("polyline")).toBeTruthy();
  });

  it("single value still renders", () => {
    render(<Sparkline values={[5]} />);
    expect(screen.getByTestId("sparkline")).toBeInTheDocument();
  });
});
