import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReleaseHistory } from "./ReleaseHistory";
import type { ReleaseOut } from "@/lib/api/client";

const mkRelease = (overrides: Partial<ReleaseOut> = {}): ReleaseOut =>
  ({
    id: `rel-${Math.random().toString(36).slice(2, 8)}`,
    connector_id: "conn-1",
    version: 1,
    spec: { foo: "bar" },
    parent_release_id: null,
    provenance_build_run_id: null,
    provenance_repair_run_id: null,
    verification_report_id: null,
    signature: "abc123signature000",
    created_at: new Date().toISOString(),
    ...overrides,
  }) as ReleaseOut;

describe("ReleaseHistory", () => {
  it("renders empty state", () => {
    render(<ReleaseHistory releases={[]} />);
    expect(screen.getByTestId("release-history-empty")).toBeInTheDocument();
  });

  it("renders releases newest first with version and signature", () => {
    const r1 = mkRelease({ id: "rel-1", version: 1, signature: "sig-aaa-111" });
    const r2 = mkRelease({ id: "rel-2", version: 2, signature: "sig-bbb-222" });
    render(<ReleaseHistory releases={[r1, r2]} />);
    expect(screen.getByTestId("release-history")).toBeInTheDocument();
    expect(screen.getByTestId("release-history-table")).toBeInTheDocument();
    expect(screen.getByTestId("release-row-rel-1")).toBeInTheDocument();
    expect(screen.getByTestId("release-row-rel-2")).toBeInTheDocument();
    expect(screen.getByText("r1")).toBeInTheDocument();
    expect(screen.getByText("r2")).toBeInTheDocument();
    // Newest non-active release is the candidate waiting for approval
    expect(screen.getByText("Candidate", { exact: false })).toBeInTheDocument();
  });

  it("marks active release", () => {
    const r1 = mkRelease({ id: "rel-1", version: 1 });
    const r2 = mkRelease({ id: "rel-2", version: 2 });
    render(<ReleaseHistory releases={[r1, r2]} activeReleaseId="rel-2" />);
    expect(screen.getByTestId("release-active-rel-2")).toBeInTheDocument();
    expect(screen.getByTestId("release-active-rel-2")).toHaveTextContent("active");
    expect(screen.queryByTestId("release-active-rel-1")).not.toBeInTheDocument();
  });

  it("shows who approved each release", () => {
    const r1 = mkRelease({ id: "rel-1", version: 1, approved_by: "alice" });
    const r2 = mkRelease({ id: "rel-2", version: 2, approved_by: null });
    render(<ReleaseHistory releases={[r1, r2]} activeReleaseId="rel-2" />);
    expect(screen.getByTestId("release-approved-by-rel-1")).toHaveTextContent("approved by alice");
    expect(screen.queryByTestId("release-approved-by-rel-2")).not.toBeInTheDocument();
  });

  it("shows no active badge when active id does not match", () => {
    const r1 = mkRelease({ id: "rel-1", version: 1 });
    render(<ReleaseHistory releases={[r1]} activeReleaseId="rel-99" />);
    expect(screen.queryByTestId("release-active-rel-1")).not.toBeInTheDocument();
  });
});
