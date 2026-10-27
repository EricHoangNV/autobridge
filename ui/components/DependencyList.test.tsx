import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DependencyList } from "./DependencyList";
import type { DependencyOut } from "@/lib/api/client";

const mkDep = (overrides: Partial<DependencyOut> = {}): DependencyOut =>
  ({
    id: `dep-${Math.random().toString(36).slice(2, 8)}`,
    connector_id: "conn-1",
    consumer_id: "consumer-a",
    entity: "Reservation",
    policy: "pause_on_release_change",
    paused: false,
    created_at: new Date().toISOString(),
    ...overrides,
  }) as DependencyOut;

describe("DependencyList", () => {
  it("renders empty state", () => {
    render(<DependencyList dependencies={[]} />);
    expect(screen.getByTestId("dependency-list-empty")).toBeInTheDocument();
  });

  it("renders dependency rows", () => {
    const deps = [
      mkDep({ id: "dep-1", consumer_id: "etl-job-a", entity: "Reservation", policy: "pause_on_release_change", paused: false }),
      mkDep({ id: "dep-2", consumer_id: "etl-job-b", entity: "FolioCharge", policy: "pause_on_release_change", paused: true }),
    ];
    render(<DependencyList dependencies={deps} />);
    expect(screen.getByTestId("dependency-list")).toBeInTheDocument();
    expect(screen.getByTestId("dependency-row-dep-1")).toBeInTheDocument();
    expect(screen.getByTestId("dependency-row-dep-2")).toBeInTheDocument();
    expect(screen.getByText("etl-job-a")).toBeInTheDocument();
    expect(screen.getByText("etl-job-b")).toBeInTheDocument();
  });

  it("shows paused vs active badge", () => {
    const deps = [
      mkDep({ id: "dep-1", paused: true }),
      mkDep({ id: "dep-2", paused: false }),
    ];
    render(<DependencyList dependencies={deps} />);
    expect(screen.getByText("paused")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });
});
