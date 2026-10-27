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
});
