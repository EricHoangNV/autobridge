import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import RepairsPage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

describe("RepairsPage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows loading", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true } as unknown as ReturnType<typeof useSWR>);
    render(<RepairsPage />);
    expect(screen.getByText(/Loading repairs/)).toBeInTheDocument();
  });

  it("shows empty when no repairs", () => {
    mockedUseSWR.mockReturnValue({ data: [], error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<RepairsPage />);
    expect(screen.getByTestId("repairs-empty")).toBeInTheDocument();
  });

  it("renders repairs table", () => {
    mockedUseSWR.mockReturnValue({
      data: [
        { id: "rep-1", drift_event_ids: [], ladder_level_reached: 1, rule_id: "rename_field", hypothesis_ids: [], visible_test_results: [], decision: "propose", model_usage: {}, created_at: new Date().toISOString() },
        { id: "rep-2", drift_event_ids: [], ladder_level_reached: 4, rule_id: null, hypothesis_ids: [], visible_test_results: [], decision: "escalate", model_usage: {}, created_at: new Date().toISOString() },
      ],
      error: undefined,
      isLoading: false,
    } as unknown as ReturnType<typeof useSWR>);
    render(<RepairsPage />);
    expect(screen.getByTestId("repair-row-rep-1")).toBeInTheDocument();
    expect(screen.getByTestId("repair-row-rep-2")).toBeInTheDocument();
  });

  it("shows error", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: new Error("fail"), isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<RepairsPage />);
    expect(screen.getByTestId("repairs-error")).toBeInTheDocument();
  });
});
