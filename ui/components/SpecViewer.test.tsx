import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SpecViewer, specToYamlString } from "./SpecViewer";

describe("specToYamlString", () => {
  it("serializes flat spec to YAML", () => {
    const y = specToYamlString({ name: "Atlas", version: 1 } as Record<string, unknown>);
    expect(y).toContain("name:");
    expect(y).toContain("Atlas");
    expect(y).toContain("version:");
  });

  it("handles nested objects and arrays", () => {
    const y = specToYamlString({
      connector: { base_url: "http://example.com", auth: { kind: "bearer" } },
      entities: ["Reservation"],
    } as Record<string, unknown>);
    expect(y).toContain("connector:");
    expect(y).toContain("base_url:");
    expect(y).toContain("entities:");
    expect(y).toContain("Reservation");
  });

  it("handles empty spec", () => {
    const y = specToYamlString({});
    expect(y).toBe("");
  });
});

describe("SpecViewer", () => {
  it("renders empty state when spec is null", () => {
    render(<SpecViewer spec={null} />);
    expect(screen.getByTestId("spec-viewer-empty")).toBeInTheDocument();
  });

  it("renders empty state when spec is empty", () => {
    render(<SpecViewer spec={{}} />);
    expect(screen.getByTestId("spec-viewer-empty")).toBeInTheDocument();
  });

  it("renders YAML viewer for valid spec", () => {
    render(<SpecViewer spec={{ name: "Test", base_url: "http://example.com" } as Record<string, unknown>} title="Test Spec" />);
    expect(screen.getByTestId("spec-viewer")).toBeInTheDocument();
    expect(screen.getByTestId("spec-yaml")).toBeInTheDocument();
    expect(screen.getByTestId("spec-yaml").textContent).toContain("name");
    expect(screen.getByText("Copy YAML")).toBeInTheDocument();
    expect(screen.getByText("Test Spec")).toBeInTheDocument();
  });

  it("shows JSON toggle", () => {
    render(<SpecViewer spec={{ foo: "bar" } as Record<string, unknown>} />);
    const toggle = screen.getByTestId("spec-json-toggle");
    expect(toggle).toBeInTheDocument();
    expect(toggle.textContent).toContain("Show raw JSON");
  });

  it("renders default title when not provided", () => {
    render(<SpecViewer spec={{ x: 1 } as Record<string, unknown>} />);
    expect(screen.getByText("Spec (YAML)")).toBeInTheDocument();
  });
});
