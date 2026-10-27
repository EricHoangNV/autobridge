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
    expect(screen.getByText("New connector")).toBeInTheDocument();
    expect(screen.getByTestId("new-connector-form")).toBeInTheDocument();
    expect(screen.getByTestId("back-to-connectors")).toHaveAttribute("href", "/connectors");
  });
});
