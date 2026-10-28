import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import SimulatorsPage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

const SIMULATORS = {
  simulators: [
    {
      id: "sim-atlas",
      display_name: "AtlasPMS",
      api_style: "Cloudbeds-style method REST",
      public_port: 8105,
      docs_source: "simulators/docs/atlas-openapi.yaml",
      developers_url: "http://localhost:8105/developers",
      live: true,
      active_scenario: null,
      connectors: [{ id: "c1", name: "Atlas ref" }],
    },
    {
      id: "sim-fias-pabx",
      display_name: "FIAS PABX",
      api_style: "FIAS over TCP",
      public_port: 8118,
      docs_source: "simulators/docs/fias-pabx.md",
      developers_url: "http://localhost:8118/developers",
      live: false,
      active_scenario: "fias_record_layout_change",
      connectors: [],
    },
  ],
};

describe("SimulatorsPage", () => {
  it("renders one row per registry simulator with live status, fault and connectors", () => {
    mockedUseSWR.mockReturnValue({ data: SIMULATORS, error: undefined });
    render(<SimulatorsPage />);
    expect(screen.getByTestId("simulators-page")).toBeInTheDocument();
    expect(screen.getByTestId("simulators-row-sim-atlas")).toBeInTheDocument();
    expect(screen.getByTestId("simulators-row-sim-fias-pabx")).toBeInTheDocument();
    // Display metadata comes from the registry, never hand-kept here.
    expect(screen.getByTestId("simulators-name-sim-atlas")).toHaveTextContent("AtlasPMS");
    expect(screen.getByTestId("simulators-name-sim-fias-pabx")).toHaveTextContent("FIAS PABX");
    expect(screen.getByTestId("simulators-live-sim-atlas")).toHaveTextContent("up");
    expect(screen.getByTestId("simulators-live-sim-fias-pabx")).toHaveTextContent("down");
    expect(screen.getByTestId("simulators-fault-sim-atlas")).toHaveTextContent("none");
    expect(screen.getByTestId("simulators-fault-sim-fias-pabx")).toHaveTextContent(
      "fias_record_layout_change",
    );
    expect(screen.getByTestId("simulators-usedby-sim-atlas")).toHaveTextContent("Atlas ref");
    expect(screen.getByTestId("simulators-usedby-sim-fias-pabx")).toHaveTextContent(
      "no connectors yet",
    );
    expect(screen.getByTestId("simulators-docs-sim-fias-pabx")).toHaveAttribute(
      "href",
      "http://localhost:8118/developers",
    );
    expect(screen.getByTestId("simulators-port-sim-fias-pabx")).toHaveTextContent("8118");
  });

  it("shows loading and error states", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined });
    const { unmount } = render(<SimulatorsPage />);
    expect(screen.getByTestId("simulators-loading")).toBeInTheDocument();
    unmount();
    mockedUseSWR.mockReturnValue({ data: undefined, error: new Error("down") });
    render(<SimulatorsPage />);
    expect(screen.getByTestId("simulators-error")).toBeInTheDocument();
  });
});
