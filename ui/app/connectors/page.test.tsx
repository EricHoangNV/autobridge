import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Mock SWR to avoid real fetches
vi.mock("swr", () => ({
  default: vi.fn(),
}));

import useSWR from "swr";
import ConnectorsPage from "./page";

const mockedUseSWR = useSWR as unknown as ReturnType<typeof vi.fn>;

describe("ConnectorsPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Default for the repairs + drift fetches (fourth/fifth SWR calls); the
    // per-test mockReturnValueOnce queues take precedence for the first three.
    mockedUseSWR.mockReturnValue({ data: [], error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>);
  });

  it("shows loading state", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: undefined, isLoading: true } as unknown as ReturnType<typeof useSWR>);
    render(<ConnectorsPage />);
    expect(screen.getByTestId("connectors-loading")).toBeInTheDocument();
  });

  it("shows error state", () => {
    mockedUseSWR.mockReturnValue({ data: undefined, error: new Error("network fail"), isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<ConnectorsPage />);
    expect(screen.getByTestId("connectors-error")).toBeInTheDocument();
  });

  it("renders connectors after load", async () => {
    const connectors = [
      { id: "c1", name: "Atlas", health_status: "healthy", base_url: "http://sim-atlas:8000", active_release_id: "rel-1", credential_ref: "K", target_entities: [], runtime_target: {}, vendor_docs_ref: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
    ];
    // First call: connectors, second: model-usage, third: details
    mockedUseSWR
      .mockReturnValueOnce({ data: connectors, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: null, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: {}, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<ConnectorsPage />);
    await waitFor(() => expect(screen.getByTestId("connectors-page")).toBeInTheDocument());
    expect(screen.getByText("Connectors")).toBeInTheDocument();
    expect(screen.getByTestId("new-connector-link")).toBeInTheDocument();
  });

  it("counts error connectors in the header and links to the wait (REQ-048)", async () => {
    const connectors = [
      { id: "c-h", name: "HarborPMS", state: "error", health_status: "healthy", base_url: "http://x", active_release_id: "r1", credential_ref: "K", target_entities: [], runtime_target: {}, vendor_docs_ref: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
      { id: "c-a", name: "AtlasPMS", state: "working", health_status: "healthy", base_url: "http://y", active_release_id: "r2", credential_ref: "K", target_entities: [], runtime_target: {}, vendor_docs_ref: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
    ];
    mockedUseSWR
      .mockReturnValueOnce({ data: connectors, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: null, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: {}, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: [], error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<ConnectorsPage />);
    await waitFor(() => expect(screen.getByTestId("connectors-headline")).toBeInTheDocument());
    expect(screen.getByTestId("connectors-headline")).toHaveTextContent("1 waiting for you.");
    expect(screen.getByTestId("connectors-waiting-link").getAttribute("href")).toBe("/connectors/c-h");
  });

  it("says nothing is waiting when no connector is in error (REQ-048)", async () => {
    const connectors = [
      { id: "c-a", name: "AtlasPMS", state: "working", health_status: "healthy", base_url: "http://y", active_release_id: "r2", credential_ref: "K", target_entities: [], runtime_target: {}, vendor_docs_ref: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
    ];
    mockedUseSWR
      .mockReturnValueOnce({ data: connectors, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: null, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>)
      .mockReturnValueOnce({ data: {}, error: undefined, isLoading: false } as unknown as ReturnType<typeof useSWR>);
    render(<ConnectorsPage />);
    await waitFor(() => expect(screen.getByTestId("connectors-headline")).toBeInTheDocument());
    expect(screen.getByTestId("connectors-headline")).toHaveTextContent("nothing waiting for you.");
    expect(screen.queryByTestId("connectors-waiting-link")).not.toBeInTheDocument();
  });
});
