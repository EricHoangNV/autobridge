import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  usePathname: () => "/connectors",
}));

import { TopBar } from "./TopBar";

describe("TopBar", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }) as unknown as typeof fetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("renders the wordmark, nav, LIVE indicator, labelled demo button and healthz", async () => {
    render(<TopBar />);
    expect(screen.getByTestId("topbar")).toBeInTheDocument();
    expect(screen.getByText("AUTOBRIDGE")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connectors" })).toHaveAttribute("href", "/connectors");
    expect(screen.getByTestId("topbar-demo-inject")).toHaveTextContent("Demo: inject fault");
    expect(screen.getByTestId("topbar-healthz")).toHaveAttribute("href", "/healthz");
    await waitFor(() => expect(screen.getByTestId("topbar-live")).toHaveTextContent("LIVE"));
  });

  it("shows OFFLINE when the health check fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")) as unknown as typeof fetch);
    render(<TopBar />);
    await waitFor(() => expect(screen.getByTestId("topbar-live")).toHaveTextContent("OFFLINE"));
  });

  it("demo button opens the drawer toggle when the drawer is on the page", async () => {
    const onToggle = vi.fn();
    const drawer = document.createElement("div");
    drawer.setAttribute("data-testid", "inject-fault-drawer");
    const toggle = document.createElement("button");
    toggle.setAttribute("data-testid", "inject-fault-toggle");
    toggle.addEventListener("click", onToggle);
    drawer.appendChild(toggle);
    document.body.appendChild(drawer);

    render(<TopBar />);
    fireEvent.click(screen.getByTestId("topbar-demo-inject"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
