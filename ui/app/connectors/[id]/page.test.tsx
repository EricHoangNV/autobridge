import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import ConnectorDetailPage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

const mockDetail = {
  id: "conn-abc-123",
  name: "Atlas PMS Staging",
  base_url: "http://sim-atlas:8000",
  credential_ref: "ATLAS_API_KEY",
  target_entities: ["Reservation"],
  runtime_target: { kind: "local_process" },
  active_release_id: "rel-1",
  health_status: "healthy",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  vendor_docs_ref: null,
  releases: [
    {
      id: "rel-1",
      connector_id: "conn-abc-123",
      version: 2,
      spec: { name: "Atlas", version: 2 },
      parent_release_id: null,
      provenance_build_run_id: null,
      provenance_repair_run_id: null,
      verification_report_id: null,
      signature: "sig-abc-1234567890",
      created_at: new Date().toISOString(),
    },
    {
      id: "rel-2",
      connector_id: "conn-abc-123",
      version: 1,
      spec: { name: "Atlas", version: 1 },
      parent_release_id: null,
      provenance_build_run_id: null,
      provenance_repair_run_id: null,
      verification_report_id: null,
      signature: "sig-xyz-7890123456",
      created_at: new Date().toISOString(),
    },
  ],
  last_sync: {
    id: "sync-1",
    release_id: "rel-1",
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    records_observed: { reservation: 10 },
    records_emitted: { reservation: 10 },
    checkpoint: {},
    invariant_results: [],
    outcome: "Complete",
    unmet_criteria: [],
    data_readiness: {},
    fingerprint_id: "fp-123",
    kind: "sync",
  },
  open_drift: [],
  dependencies: [
    {
      id: "dep-1",
      connector_id: "conn-abc-123",
      consumer_id: "downstream-etl",
      entity: "Reservation",
      policy: "pause_on_release_change",
      paused: false,
      created_at: new Date().toISOString(),
    },
  ],
};

const mockSyncRuns = [
  {
    id: "sync-1",
    release_id: "rel-1",
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    records_observed: { reservation: 10 },
    records_emitted: { reservation: 10 },
    checkpoint: {},
    invariant_results: [],
    outcome: "Complete",
    unmet_criteria: [],
    data_readiness: {},
    fingerprint_id: "fp-123",
    kind: "sync",
  },
  {
    id: "sync-2",
    release_id: "rel-1",
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    records_observed: {},
    records_emitted: {},
    checkpoint: {},
    invariant_results: [],
    outcome: "Degraded",
    unmet_criteria: ["pagination_incomplete"],
    data_readiness: {},
    fingerprint_id: "fp-456",
    kind: "sync",
  },
];

describe("ConnectorDetailPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Default for the repairs fetch (third SWR call); the per-test
    // mockReturnValueOnce queues take precedence for detail + sync-runs.
    mockedUseSWR.mockReturnValue({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    // Mock fetch for trigger-sync button (used inside handleSync)
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}), text: () => Promise.resolve("") }) as unknown as typeof fetch;
  });

  it("renders connector detail with spec viewer, releases, sync runs, fingerprints, dependencies", () => {
    // First SWR call: connector detail, second: sync-runs
    mockedUseSWR
      .mockReturnValueOnce({ data: mockDetail, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: mockSyncRuns, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    render(<ConnectorDetailPage params={{ id: "conn-abc-123" }} />);

    expect(screen.getByTestId("connector-detail-page")).toBeInTheDocument();
    expect(screen.getByTestId("connector-name")).toHaveTextContent("Atlas PMS Staging");
    expect(screen.getByTestId("back-to-connectors")).toHaveAttribute("href", "/connectors");
    expect(screen.getByTestId("connector-credential-ref")).toHaveTextContent("ATLAS_API_KEY");
    expect(screen.getByTestId("connector-target-entities")).toHaveTextContent("Reservation");
    // Spec viewer
    expect(screen.getByTestId("spec-viewer")).toBeInTheDocument();
    // Release history
    expect(screen.getByTestId("release-history")).toBeInTheDocument();
    expect(screen.getByTestId("release-history-table")).toBeInTheDocument();
    // Sync runs (from sync-runs endpoint)
    expect(screen.getByTestId("sync-runs-table")).toBeInTheDocument();
    expect(screen.getByTestId("sync-run-row-sync-1")).toBeInTheDocument();
    // Fingerprints section
    expect(screen.getByTestId("fingerprints-section")).toBeInTheDocument();
    // Dependency registrations
    expect(screen.getByTestId("dependency-list")).toBeInTheDocument();
    // Trigger sync button
    expect(screen.getByTestId("sync-now-button")).toBeInTheDocument();
  });

  it("renders Degraded sync run correctly", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockDetail, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: mockSyncRuns, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    render(<ConnectorDetailPage params={{ id: "conn-abc-123" }} />);
    // Degraded run should be present
    expect(screen.getByTestId("sync-run-row-sync-2")).toBeInTheDocument();
  });

  it("groups fingerprints, collapsed by default with a one-line summary", async () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockDetail, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: mockSyncRuns, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    const { fireEvent } = await import("@testing-library/react");
    render(<ConnectorDetailPage params={{ id: "conn-abc-123" }} />);
    // Two distinct fingerprints → two groups, collapsed: no per-run entries.
    expect(screen.getByTestId("fingerprints-section")).toBeInTheDocument();
    expect(screen.getByTestId("fingerprints-summary")).toHaveTextContent("2 fingerprints");
    expect(screen.getByTestId("fingerprint-group-0")).toHaveTextContent("1 sync");
    expect(screen.queryByTestId("fingerprint-entry-sync-1")).not.toBeInTheDocument();
    // Expand the first group: run ids appear with full values on hover.
    fireEvent.click(screen.getByTestId("fingerprint-group-toggle-0"));
    const entry = screen.getByTestId("fingerprint-entry-sync-1");
    expect(entry).toBeInTheDocument();
    expect(entry.querySelector("code")).toHaveAttribute("title", "sync-1");
  });

  it("spec viewer shows full release id and signature on hover; toc jumps inside it", async () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockDetail, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: mockSyncRuns, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    const { fireEvent } = await import("@testing-library/react");
    render(<ConnectorDetailPage params={{ id: "conn-abc-123" }} />);
    const viewerTitle = screen.getByText("Spec — r2 (rel-1)");
    expect(viewerTitle.title).toContain("rel-1");
    expect(viewerTitle.title).toContain("Signature");
    expect(screen.getByTestId("release-selector").getAttribute("title")).toContain("rel-1");
    // Toc buttons scroll inside the fixed-height viewer (no throw without layout).
    fireEvent.click(screen.getByTestId("spec-toc-name"));
    expect(screen.getByTestId("spec-yaml")).toBeInTheDocument();
  });

  it("shows error when connector not found", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: undefined, error: new Error("404 not found"), isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    render(<ConnectorDetailPage params={{ id: "nonexistent" }} />);
    expect(screen.getByTestId("connector-detail-error")).toBeInTheDocument();
  });

  it("renders release selector when multiple releases", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockDetail, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: mockSyncRuns, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    render(<ConnectorDetailPage params={{ id: "conn-abc-123" }} />);
    expect(screen.getByTestId("release-selector")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: true, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);

    render(<ConnectorDetailPage params={{ id: "conn-abc-123" }} />);
    expect(screen.getByText("Loading connector…")).toBeInTheDocument();
  });
});
