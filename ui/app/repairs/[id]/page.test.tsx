import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

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
  afterEach(() => vi.unstubAllGlobals());

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
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("repair-detail-page")).toBeInTheDocument();
    expect(screen.getByTestId("repair-title")).toBeInTheDocument();
    expect(screen.getByTestId("ladder-trace")).toBeInTheDocument();
    expect(screen.getByTestId("patch-diff")).toBeInTheDocument();
    expect(screen.getByTestId("repair-actions")).toBeInTheDocument();
    expect(screen.getByTestId("inject-fault-drawer")).toBeInTheDocument();
    // P2.S6.1 richer views: graph renders from drift links, chart shows its
    // empty state until /metrics/model-usage resolves.
    expect(screen.getByTestId("evidence-graph")).toBeInTheDocument();
    expect(screen.getByTestId("model-usage-chart-empty")).toBeInTheDocument();
  });

  it("shows drift evidence section", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockRepair, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("drift-evidence-section")).toBeInTheDocument();
  });

  it("shows model usage with zero-frontier badge", () => {
    mockedUseSWR
      .mockReturnValueOnce({ data: mockRepair, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [mockDrift], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
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
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() } as unknown as ReturnType<typeof useSWR>);
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(screen.getByTestId("back-to-repairs")).toBeInTheDocument();
  });

  it("hides verdict and action while climbing without a patch", () => {
    const climbingRepair = {
      ...mockRepair,
      id: "repair-climbing-1",
      ladder_level_reached: 2,
      rule_id: null,
      candidate_patch: null,
      visible_test_results: [],
      verifier_report_id: null,
    };
    // Key-based mock: survives the re-renders that live updates cause.
    mockedUseSWR.mockImplementation(
      ((key: unknown) => {
        const base = { error: undefined, isLoading: false, mutate: vi.fn() };
        if (typeof key === "string" && key.startsWith("drift-for-repair")) return { ...base, data: [mockDrift] };
        if (typeof key === "string" && key.startsWith("evidence-for-repair")) return { ...base, data: [] };
        if (typeof key === "string" && key.startsWith("/repairs/")) return { ...base, data: climbingRepair };
        return { ...base, data: undefined, isLoading: true };
      }) as unknown as typeof useSWR,
    );
    render(<RepairDetailPage params={{ id: "repair-climbing-1" }} />);
    expect(screen.getByTestId("repair-detail-page")).toHaveAttribute("data-state", "climbing");
    expect(screen.getByTestId("repair-title")).toHaveTextContent("Climbing");
    expect(screen.getByText(/Not run yet/)).toBeInTheDocument();
    const approve = screen.getByTestId("repair-approve-btn");
    expect(approve).toBeDisabled();
    expect(approve).toHaveTextContent("No patch to approve yet");
    // 0 "so far" is not the verdict yet: no zero badge, counter holds at 0.
    expect(screen.queryByTestId("model-usage-zero-frontier")).not.toBeInTheDocument();
    expect(screen.getByTestId("model-usage-frontier-tokens")).toHaveTextContent("0");
  });

  it("advances the rung from live SSE ladder events", () => {
    const listeners = new Map<string, Array<(e: { data: string }) => void>>();
    class FakeEventSource {
      onmessage: ((e: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public url: string) {
        FakeEventSource.live.push(this);
      }
      addEventListener(type: string, fn: (e: { data: string }) => void) {
        const arr = listeners.get(type) ?? [];
        arr.push(fn);
        listeners.set(type, arr);
      }
      removeEventListener() {}
      close() {}
      static live: FakeEventSource[] = [];
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    mockedUseSWR.mockImplementation(
      ((key: unknown) => {
        const base = { error: undefined, isLoading: false, mutate: vi.fn() };
        if (typeof key === "string" && key.startsWith("drift-for-repair")) return { ...base, data: [mockDrift] };
        if (typeof key === "string" && key.startsWith("evidence-for-repair")) return { ...base, data: [] };
        if (typeof key === "string" && key.startsWith("/repairs/")) return { ...base, data: mockRepair };
        return { ...base, data: undefined, isLoading: true };
      }) as unknown as typeof useSWR,
    );
    render(<RepairDetailPage params={{ id: "repair-abc-123" }} />);
    expect(FakeEventSource.live.length).toBeGreaterThan(0);
    expect(FakeEventSource.live[0].url).toContain("/runs/sync-1/events");
    expect(screen.getByTestId("ladder-level-badge")).toHaveTextContent("L1");
    act(() => {
      for (const fn of listeners.get("hypothesis") ?? []) {
        fn({ data: JSON.stringify({ id: "h-1", kind: "hypothesis", payload: { prior: 0.4 }, run_id: "sync-1" }) });
      }
    });
    expect(screen.getByTestId("ladder-level-badge")).toHaveTextContent("L2");
  });
});
