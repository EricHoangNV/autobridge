import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  FingerprintGroups,
  groupSyncRunsByFingerprint,
  fingerprintSummary,
  formatRange,
} from "./FingerprintGroups";

const run = (id: string, fp: string | null, started_at: string, outcome = "Complete") => ({
  id,
  fingerprint_id: fp,
  outcome,
  started_at,
});

describe("groupSyncRunsByFingerprint", () => {
  it("merges consecutive runs with the same fingerprint", () => {
    const runs = [
      run("s1", "fp-aaa", "2026-10-30T09:05:00"),
      run("s2", "fp-aaa", "2026-10-30T09:06:00"),
      run("s3", "fp-aaa", "2026-10-30T09:07:00"),
    ];
    const groups = groupSyncRunsByFingerprint(runs);
    expect(groups).toHaveLength(1);
    expect(groups[0].runs.map((r) => r.id)).toEqual(["s1", "s2", "s3"]);
  });

  it("starts a new group on fingerprint change, even for a repeat value", () => {
    const runs = [
      run("s1", "fp-aaa", "2026-10-30T09:05:00"),
      run("s2", "fp-bbb", "2026-10-30T09:09:00", "Degraded"),
      run("s3", "fp-aaa", "2026-10-30T09:12:00"),
    ];
    const groups = groupSyncRunsByFingerprint(runs);
    expect(groups).toHaveLength(3);
    expect(groups[1].fingerprint).toBe("fp-bbb");
    expect(groups[1].outcome).toBe("Degraded");
  });

  it("skips runs without a fingerprint", () => {
    const groups = groupSyncRunsByFingerprint([run("s1", null, "2026-10-30T09:05:00")]);
    expect(groups).toHaveLength(0);
  });

  it("orders oldest first regardless of input order", () => {
    const runs = [
      run("s2", "fp-aaa", "2026-10-30T09:06:00"),
      run("s1", "fp-aaa", "2026-10-30T09:05:00"),
    ];
    const groups = groupSyncRunsByFingerprint(runs);
    expect(groups[0].runs.map((r) => r.id)).toEqual(["s1", "s2"]);
  });
});

describe("fingerprintSummary", () => {
  it("summarises one group", () => {
    const groups = groupSyncRunsByFingerprint([
      run("s1", "fp-aaa", "2026-10-30T09:05:00"),
      run("s2", "fp-aaa", "2026-10-30T09:06:00"),
    ]);
    expect(fingerprintSummary(groups)).toBe("1 fingerprint · 2 syncs");
  });

  it("summarises several groups with the last change time", () => {
    const groups = groupSyncRunsByFingerprint([
      run("s1", "fp-aaa", "2026-10-30T09:05:00"),
      run("s2", "fp-bbb", "2026-10-30T21:09:00", "Degraded"),
    ]);
    expect(fingerprintSummary(groups)).toContain("2 fingerprints · last change");
  });
});

describe("formatRange", () => {
  it("collapses the shared AM/PM period", () => {
    expect(formatRange("2026-10-30T09:05:00", "2026-10-30T21:32:00")).toContain("→");
  });

  it("returns a single time for identical bounds", () => {
    expect(formatRange("2026-10-30T09:05:00", "2026-10-30T09:05:00")).not.toContain("→");
  });
});

describe("FingerprintGroups", () => {
  const groups = groupSyncRunsByFingerprint([
    ...Array.from({ length: 28 }, (_, i) =>
      run(`sync-same-${i}`, "3520bd6c039e71e2", `2026-10-30T09:${String(5 + i).padStart(2, "0")}:00`),
    ),
    run("sync-new-0", "77b78e79039e71e2", "2026-10-30T21:09:00", "Degraded"),
  ]);

  it("renders one row per group with counts", () => {
    render(<FingerprintGroups groups={groups} />);
    expect(screen.getByTestId("fingerprint-groups")).toBeInTheDocument();
    expect(screen.getByTestId("fingerprint-group-0")).toHaveTextContent("28 syncs");
    expect(screen.getByTestId("fingerprint-group-1")).toHaveTextContent("1 sync");
  });

  it("highlights the change group and links its drift repair", () => {
    render(
      <FingerprintGroups
        groups={groups}
        driftBySyncRunId={new Map([["sync-new-0", { driftId: "drift-1", driftClass: "X", repairId: "rep-1" }]])}
      />,
    );
    const change = screen.getByTestId("fingerprint-group-1");
    expect(change).toHaveTextContent("changed at");
    expect(screen.getByTestId("fingerprint-group-drift-link-1")).toHaveAttribute("href", "/repairs/rep-1");
  });

  it("expands run ids on toggle and shows full values on hover", () => {
    render(<FingerprintGroups groups={groups} />);
    expect(screen.queryByTestId("fingerprint-entry-sync-same-0")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("fingerprint-group-toggle-0"));
    const entry = screen.getByTestId("fingerprint-entry-sync-same-0");
    expect(entry).toBeInTheDocument();
    expect(entry.querySelector("code")).toHaveAttribute("title", "sync-same-0");
  });

  it("group fingerprint shows the full value on hover", () => {
    render(<FingerprintGroups groups={groups} />);
    const code = screen.getByTestId("fingerprint-group-0").querySelector("code");
    expect(code).toHaveAttribute("title", "3520bd6c039e71e2");
  });
});
