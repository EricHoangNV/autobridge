import { describe, it, expect } from "vitest";
import {
  DRIFT_PLAIN,
  CHECK_PLAIN,
  RUNG_CHIP,
  driftPlain,
  checkPlain,
  rungChip,
  rungHeadline,
  stateHeadline,
  patchChangePlain,
  storylineStep1,
  storylineStep2,
  storylineStep3,
  DRY_RUN_PASSED,
  dryRunRetried,
} from "./repair-plain";

// Every drift class used in simulators/scenarios/*.yaml (REQ-056 item 7).
const SCENARIO_DRIFT_CLASSES = [
  "ACK_TIMEOUT",
  "AGENT_RESTART",
  "AUTH_EXPIRED",
  "AUTH_REVOKED",
  "CAPABILITY_REMOVED",
  "CLOUD_SLOW",
  "DUPLICATE_DELIVERY",
  "LINK_RESYNC",
  "PAGINATION_BUG",
  "PAGINATION_CHANGE",
  "PROTOCOL_VIOLATION",
  "RATE_LIMIT",
  "SCHEMA_RENAME",
  "SEMANTIC_VALUE_DRIFT",
  "SESSION_LOST",
  "STALE_COMMAND",
  "STATUS_VOCAB",
  "TRANSIENT_ERROR",
];

// Every check kind the engine verifies.
const CHECK_KINDS = [
  "money_invariant",
  "count_invariant",
  "no_loss",
  "unique_key",
  "date_order",
  "business_date_invariant",
  "http_error",
  "feed_unavailable",
];

describe("repair-plain lookup tables (REQ-056 item 6)", () => {
  it("covers every drift class used in scenarios", () => {
    for (const dc of SCENARIO_DRIFT_CLASSES) {
      expect(DRIFT_PLAIN[dc], dc).toBeTruthy();
      expect(driftPlain(dc)).not.toBe("");
      expect(driftPlain(dc)).not.toContain("_");
    }
  });

  it("covers every check kind", () => {
    for (const kind of CHECK_KINDS) {
      expect(CHECK_PLAIN[kind], kind).toBeTruthy();
      expect(checkPlain(kind)).not.toBe("");
      expect(checkPlain(kind)).not.toContain("_");
    }
  });

  it("falls back to the code itself for unknown codes, never blank", () => {
    expect(driftPlain("SOME_FUTURE_DRIFT")).toBe("SOME_FUTURE_DRIFT");
    expect(checkPlain("some_future_check")).toBe("some_future_check");
    expect(driftPlain(null)).toBe("Drift detected");
    expect(checkPlain(null)).toBe("a check failed");
  });

  it("keeps headlines to 8 words or fewer", () => {
    for (const v of Object.values(DRIFT_PLAIN)) {
      expect(v.split(/\s+/).length, v).toBeLessThanOrEqual(8);
    }
    for (const v of Object.values(RUNG_CHIP)) {
      expect(v.split(/\s+/).length, v).toBeLessThanOrEqual(8);
    }
    expect(rungHeadline(1, false).split(/\s+/).length).toBeLessThanOrEqual(10);
    expect(stateHeadline("escalate").split(/\s+/).length).toBeLessThanOrEqual(8);
  });

  it("names all five rungs as plain chips", () => {
    expect([0, 1, 2, 3, 4].map(rungChip)).toEqual(["Known fix", "Rule", "Small model", "AI model", "You"]);
  });

  it("phrases what-changes per patch shape without internal codes", () => {
    expect(patchChangePlain("replace", "/entities/Reservation/checks/3/compare_to")).toContain("Compare");
    expect(patchChangePlain("remove", "/entities/Reservation/checks/3")).toContain("Stop");
    expect(patchChangePlain("replace", "/operations/list/pagination/strategy")).toContain("pages");
    expect(patchChangePlain("replace", "/operations/list/path", )).toContain("API");
  });

  it("tells the storyline in plain words for deck 3 and deck 5", () => {
    // Deck 3: paging changed, fixed by a known rule.
    const d3s1 = storylineStep1("PAGINATION_CHANGE", null);
    expect(d3s1.headline).toBe("Paging changed");
    const d3s2 = storylineStep2(1, 0);
    expect(d3s2.headline).toMatch(/known rule/);
    expect(d3s2.fact).toMatch(/no AI/);
    const d3s3 = storylineStep3("propose", true, null);
    expect(d3s3.fact).toMatch(/verified/);
    // Deck 5: amounts no longer add up, asked the AI model.
    const d5s1 = storylineStep1("SEMANTIC_VALUE_DRIFT", "money_invariant");
    expect(d5s1.headline).toBe("Values changed meaning");
    expect(d5s1.fact).toMatch(/amounts/);
    const d5s2 = storylineStep2(3, 2);
    expect(d5s2.headline).toMatch(/AI model/);
    expect(d5s2.fact).toMatch(/2 calls/);
    const d5s3 = storylineStep3("escalate", true, "Accept net amounts");
    expect(d5s3.headline).toMatch(/Accept net/);
    expect(d5s3.fact).toMatch(/tested/);
  });

  it("has dry-run lines for both outcomes", () => {
    expect(DRY_RUN_PASSED).toMatch(/passed/);
    expect(dryRunRetried("`$.net_sum` is not sent")).toMatch(/rejected/);
    expect(dryRunRetried("`$.net_sum` is not sent")).toMatch(/passed/);
    expect(dryRunRetried("")).toMatch(/rejected/);
  });
});
