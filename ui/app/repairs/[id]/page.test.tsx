import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import RepairDetailPage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

const mockRepair = {
  id: "repair-abc-123",
  drift_event_ids: ["drift-1"],
  ladder_level_reached: 1,
  candidate_patch: [{ op: "replace", path: "/operations/list/pagination/strategy", value: "cursor" }],
  rule_id: "offset_to_cursor",
  hypothesis_ids: ["hyp-1"],
  visible_test_results: [{ name: "records_match", passed: true }],
  verifier_report_id: "report-xyz",
  decision: "propose",
  model_usage: { frontier_tokens: 0, small_tokens: 0, calls: 0 },
  escalation_bundle: null,
  created_at: new Date().toISOString(),
};

const mockDrift = {
  id: "drift-1",
  connector_id: "c1",
  drift_class: "PAGINATION_CHANGED",
  severity: "high",
  evidence_ids: ["ev-1"],
  source_sync_run_id: "sync-1",
  status: "open",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

describe("RepairDetailPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows loading when params is Promise", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={Promise.resolve({ id: "repair-abc-123" })} />);
    expect(screen.getByTestId("repair-detail-page")).toBeInTheDocument();
  });

  it("renders repair detail with all sections", () => {
    // First SWR: repair, second: drift events, third: evidence
    mockedUseSWR
      .mockReturnValueOnce({ data: mockRepair, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-detail-page")).toBeInTheDocument();
    expect(screen.getByTestId("repair-title")).toBeInTheDocument();
    expect(screen.getByTestId("ladder-trace")).toBeInTheDocument();
    expect(screen.getByTestId("patch-diff")).toBeInTheDocument();
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-drawer")).toBeInTheDocument();
  });

  it("shows drift evidence section", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockRepair, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("drift-evidence-section")).toBeInTheDocument();
  });

  it("shows model usage with zero-frontier badge", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockRepair, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getAllByTestId("model-usage-zero-frontier").length).toBeGreaterThanOrEqual(1);
  });

  it("shows error when repair fetch fails", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: new Error("not found"), isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "missing" }} />);
    expect(screen.getByTestId("repair-detail-error")).toBeInTheDocument();
  });

  it("has back navigation", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockRepair, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("back-to-repairs")).toBeInTheDocument();
  });
});
