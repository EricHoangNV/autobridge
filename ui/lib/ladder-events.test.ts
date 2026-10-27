import { describe, it, expect } from "vitest";
import { ladderLevelFromEvent, maxLadderLevel, LADDER_EVENT_KINDS } from "./ladder-events";

describe("ladderLevelFromEvent", () => {
  it("returns null for done/message and unknown kinds", () => {
    expect(ladderLevelFromEvent("done", { run_id: "r", count: 3 })).toBeNull();
    expect(ladderLevelFromEvent("message", "hello")).toBeNull();
    expect(ladderLevelFromEvent("mystery_kind", { payload: {} })).toBeNull();
    expect(ladderLevelFromEvent("hypothesis", null)).toBeNull();
  });

  it("maps drift-signal kinds to L0", () => {
    for (const kind of ["fingerprint_changed", "pagination_changed", "invariant_failure", "outcome_degraded"]) {
      expect(ladderLevelFromEvent(kind, { id: "e", kind, payload: {} })).toBe(0);
    }
  });

  it("maps hypothesis/probe/observation to L2", () => {
    expect(ladderLevelFromEvent("hypothesis", { id: "h", payload: { prior: 0.4 } })).toBe(2);
    expect(ladderLevelFromEvent("probe", { id: "p", payload: { url: "http://x/list" } })).toBe(2);
    expect(ladderLevelFromEvent("observation", { id: "o", payload: {} })).toBe(2);
  });

  it("maps rule_id payloads to L1", () => {
    expect(ladderLevelFromEvent("decision", { payload: { rule_id: "offset_to_cursor" } })).toBe(1);
  });

  it("maps spent frontier budget to L3", () => {
    expect(ladderLevelFromEvent("decision", { payload: { frontier_tokens: 8412 } })).toBe(3);
    expect(ladderLevelFromEvent("probe", { payload: { frontier_calls: 2 } })).toBe(3);
  });

  it("maps escalate decisions to L4 but not propose", () => {
    expect(ladderLevelFromEvent("decision", { payload: { decision: "escalate" } })).toBe(4);
    expect(ladderLevelFromEvent("decision", { payload: { decision: "propose" } })).toBeNull();
  });

  it("lets an explicit payload level win", () => {
    expect(ladderLevelFromEvent("hypothesis", { payload: { ladder_level: 1 } })).toBe(1);
    expect(ladderLevelFromEvent("probe", { payload: { ladder_level_reached: 3 } })).toBe(3);
    expect(ladderLevelFromEvent("observation", { payload: { level: 0 } })).toBe(0);
    // Out-of-range levels are ignored, mapping falls through.
    expect(ladderLevelFromEvent("hypothesis", { payload: { ladder_level: 9 } })).toBe(2);
  });

  it("exposes the subscribed kinds including done", () => {
    expect(LADDER_EVENT_KINDS).toContain("hypothesis");
    expect(LADDER_EVENT_KINDS).toContain("decision");
    expect(LADDER_EVENT_KINDS).toContain("done");
  });
});

describe("maxLadderLevel", () => {
  it("returns null for empty or silent batches", () => {
    expect(maxLadderLevel([])).toBeNull();
    expect(maxLadderLevel([{ type: "done", data: {} }])).toBeNull();
  });

  it("returns the highest rung proven", () => {
    expect(
      maxLadderLevel([
        { type: "pagination_changed", data: { payload: {} } },
        { type: "hypothesis", data: { payload: {} } },
        { type: "done", data: {} },
      ]),
    ).toBe(2);
  });
});
