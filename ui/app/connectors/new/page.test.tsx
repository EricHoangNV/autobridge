import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import NewConnectorPage from "./page";

describe("NewConnectorPage", () => {
  it("renders heading and form", () => {
    render(<NewConnectorPage />);
    expect(screen.getByTestId("new-connector-page")).toBeInTheDocument();
    expect(screen.getByText("Build a connector")).toBeInTheDocument();
    expect(screen.getByTestId("new-connector-form")).toBeInTheDocument();
    expect(screen.getByTestId("back-to-connectors")).toHaveAttribute("href", "/connectors");
  });

  it("renders the run sheet beside the form", () => {
    render(<NewConnectorPage />);
    expect(screen.getByTestId("run-sheet-title")).toHaveTextContent("What the build will do");
    const sheet = screen.getByTestId("run-sheet");
    expect(sheet.textContent).toContain("Read the docs");
    expect(sheet.textContent).toContain("Probe the sandbox, GET only");
    expect(sheet.textContent).toContain("You approve release r1");
    const start = screen.getByTestId("sheet-start-build");
    expect(start).toHaveAttribute("form", "build-connector-form");
    expect(screen.getByTestId("new-connector-form")).toHaveAttribute("id", "build-connector-form");
  });
});
