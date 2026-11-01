import { describe, it, expect } from "vitest";
import { deriveTrackerSteps } from "./AfterApproveTracker";

const approval = (overrides = {}) => ({
  id: "a-1",
  actor: "eric",
  decision: "approve",
  from_release_id: "rel-1",
  to_release_id: "rel-2",
  created_at: "2026-11-01T10:00:00Z",
  ...overrides,
});

const releases = [
  { id: "rel-1", version: 1 },
  { id: "rel-2", version: 2, provenance_repair_run_id: "rep-1" },
  { id: "rel-3", version: 3 },
];

const syncOn = (release_id: string, outcome: string, started_at: string) => ({
  id: `s-${release_id}-${outcome}`,
  release_id,
  outcome,
  started_at,
});

function doneMap(steps: { key: string; done: boolean }[]) {
  return Object.fromEntries(steps.map((s) => [s.key, s.done]));
}

describe("deriveTrackerSteps (REQ-073 item 4)", () => {
  it("a shipped fix shows all steps done from the real records", () => {
    const { steps, approved } = deriveTrackerSteps({
      repairId: "rep-1",
      approval: approval(),
      connector: { id: "c1", active_release_id: "rel-2", state: "working", activity: "all clear", releases },
      syncRuns: [syncOn("rel-2", "Complete", "2026-11-01T10:01:00Z")],
    });
    expect(approved).toBe(true);
    expect(doneMap(steps)).toEqual({ approval: true, activated: true, sync: true, complete: true, working: true });
    expect(steps.find((s) => s.key === "activated")?.detail).toContain("r2 is active");
    expect(steps.find((s) => s.key === "sync")?.detail).toContain("Complete");
  });

  it("a superseded candidate still reads done: a sync ran on it, so it went live", () => {
    const { steps } = deriveTrackerSteps({
      repairId: "rep-1",
      approval: approval(),
      // r1 was re-activated for a second break; a later repair shipped r3.
      connector: { id: "c1", active_release_id: "rel-3", state: "working", activity: "all clear", releases },
      syncRuns: [
        syncOn("rel-2", "Complete", "2026-11-01T10:01:00Z"),
        syncOn("rel-1", "Degraded", "2026-11-01T10:05:00Z"),
        syncOn("rel-3", "Complete", "2026-11-01T10:06:00Z"),
      ],
    });
    expect(doneMap(steps)).toEqual({ approval: true, activated: true, sync: true, complete: true, working: true });
    expect(steps.find((s) => s.key === "activated")?.detail).toContain("went live");
  });

  it("waits honestly when no sync ran on the new release yet", () => {
    const { steps } = deriveTrackerSteps({
      repairId: "rep-1",
      approval: approval(),
      connector: { id: "c1", active_release_id: "rel-2", state: "degrading", activity: "waiting", releases },
      syncRuns: [],
    });
    expect(doneMap(steps)).toEqual({ approval: true, activated: true, sync: false, complete: false, working: false });
    expect(steps.find((s) => s.key === "sync")?.detail).toContain("waiting for the next sync");
  });

  it("uses the first sync on the new release, naming a failed outcome", () => {
    const { steps } = deriveTrackerSteps({
      repairId: "rep-1",
      approval: approval(),
      connector: { id: "c1", active_release_id: "rel-2", state: "error", activity: "needs decision", releases },
      syncRuns: [syncOn("rel-2", "Degraded", "2026-11-01T10:01:00Z")],
    });
    const byKey = Object.fromEntries(steps.map((s) => [s.key, s]));
    expect(byKey.sync.done).toBe(true);
    expect(byKey.sync.detail).toContain("Degraded");
    expect(byKey.complete.done).toBe(false);
  });

  it("falls back to the provenanced release when the approval has no to_release", () => {
    const { steps } = deriveTrackerSteps({
      repairId: "rep-1",
      approval: approval({ to_release_id: null }),
      connector: { id: "c1", active_release_id: "rel-2", state: "working", activity: "all clear", releases },
      syncRuns: [syncOn("rel-2", "Complete", "2026-11-01T10:01:00Z")],
    });
    expect(doneMap(steps)).toEqual({ approval: true, activated: true, sync: true, complete: true, working: true });
  });

  it("renders nothing before approval", () => {
    const { steps, approved } = deriveTrackerSteps({
      repairId: "rep-1",
      approval: null,
      connector: { id: "c1", active_release_id: "rel-1", state: "degrading", releases },
      syncRuns: [],
    });
    expect(approved).toBe(false);
    expect(steps).toEqual([]);
  });
});
