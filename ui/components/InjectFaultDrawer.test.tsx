import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { InjectFaultDrawer } from "./InjectFaultDrawer";

describe("InjectFaultDrawer", () => {
  it("renders drawer with Demo label", () => {
    render(<InjectFaultDrawer />);
    expect(screen.getByTestId("inject-fault-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-toggle").textContent).toContain("Demo");
    expect(screen.getByTestId("inject-fault-toggle").textContent).toContain("labelled");
  });

  it("toggles open to show panel", () => {
    render(<InjectFaultDrawer />);
    expect(screen.queryByTestId("inject-fault-panel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("inject-fault-toggle"));
    expect(screen.getByTestId("inject-fault-panel")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-target")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-scenario")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-submit")).toBeInTheDocument();
  });

  it("is open by default when defaultOpen true", () => {
    render(<InjectFaultDrawer defaultOpen />);
    expect(screen.getByTestId("inject-fault-panel")).toBeInTheDocument();
  });

  it("shows scenario options", () => {
    render(<InjectFaultDrawer defaultOpen />);
    const select = screen.getByTestId("inject-fault-scenario") as HTMLSelectElement;
    expect(select.options.length).toBeGreaterThan(5);
  });

  it("has seed input", () => {
    render(<InjectFaultDrawer defaultOpen />);
    expect(screen.getByTestId("inject-fault-seed")).toBeInTheDocument();
  });

  it("uses the dashed broadcast drawer with demo-only copy", () => {
    render(<InjectFaultDrawer defaultOpen />);
    const drawer = screen.getByTestId("inject-fault-drawer");
    expect(drawer.className).toMatch(/border-dashed/);
    expect(screen.getByText("Inject a fault")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-panel").textContent).toContain("never the engine");
  });

  it("labels the submit button with the chosen scenario", () => {
    render(<InjectFaultDrawer defaultOpen />);
    expect(screen.getByTestId("inject-fault-submit").textContent).toContain("rename_guest_id");
    const select = screen.getByTestId("inject-fault-scenario") as HTMLSelectElement;
    expect(select.textContent).toContain("invalid_patch_trap");
  });

  it("offers page_to_cursor alongside the legacy offset_to_cursor (REQ-019)", () => {
    render(<InjectFaultDrawer defaultOpen />);
    const select = screen.getByTestId("inject-fault-scenario") as HTMLSelectElement;
    expect(select.textContent).toContain("page_to_cursor");
    expect(select.textContent).toContain("offset_to_cursor");
  });
});
