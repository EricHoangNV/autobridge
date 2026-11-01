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
    // A vendor change, not an "active fault": the connector may long since have adapted.
    expect(screen.getByTestId("simulators-row-sim-atlas")).toHaveTextContent("vendor change");
    expect(screen.getByTestId("simulators-row-sim-atlas")).not.toHaveTextContent("active fault");
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
    // The label is the URL itself, never the URL with /developers doubled.
    expect(screen.getByTestId("simulators-docs-sim-atlas")).toHaveTextContent(
      "localhost:8105/developers",
    );
    expect(screen.getByTestId("simulators-docs-sim-fias-pabx")).toHaveTextContent(
      "localhost:8118/developers",
    );
    expect(screen.getByTestId("simulators-port-sim-fias-pabx")).toHaveTextContent("8118");
  });

  it("colours the vendor-change note by unresolved drift, never by connector state", () => {
    const sims = {
      simulators: [
        { ...SIMULATORS.simulators[0], active_scenario: "page_to_cursor", connectors: [{ id: "c1", name: "AtlasPMS" }] },
        { ...SIMULATORS.simulators[0], id: "sim-harbor", display_name: "HarborPMS", active_scenario: "gross_to_net", connectors: [{ id: "c2", name: "HarborPMS" }] },
      ],
    };
    // Atlas is stuck on an unrelated "sync running" but its drift is repaired;
    // Harbor looks Working but its drift is still escalated.
    const states = [
      { id: "c1", state: "degrading", activity: "sync running", last_sync_at: null },
      { id: "c2", state: "working", activity: "in sync · Complete 2 s ago", last_sync_at: null },
    ];
    const drift = [
      { connector_id: "c1", status: "repaired" },
      { connector_id: "c2", status: "escalated" },
    ];
    mockedUseSWR.mockImplementation((key: string) =>
      key === "/connectors"
        ? { data: states, error: undefined }
        : key === "/drift"
          ? { data: drift, error: undefined }
          : { data: sims, error: undefined },
    );
    render(<SimulatorsPage />);
    const repaired = screen.getByTestId("simulators-fault-note-sim-atlas");
    expect(repaired).toHaveAttribute("data-adapted", "true");
    expect(repaired).toHaveTextContent("page_to_cursor is live on AtlasPMS — no open drift.");
    expect(repaired.className).not.toContain("text-stop");
    const open = screen.getByTestId("simulators-fault-note-sim-harbor");
    expect(open).toHaveAttribute("data-adapted", "false");
    expect(open).toHaveTextContent("drift still open, see Repairs");
    expect(open.className).toContain("text-stop");
    mockedUseSWR.mockReset();
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
