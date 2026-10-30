/**
 * REQ-055 item 3f — automated no-overlap / no-overflow layout check.
 *
 * Loads the connectors list, a connector page (long spec, 30+ sync runs),
 * the build page, the knowledge page, and the repair page in each state
 * (propose, climbing, escalated with proposal, approved) at 1440x900 and
 * 1280x800, and fails on:
 *   1. text overflowing its box (scrollWidth > clientWidth on elements that
 *      do not scroll by design),
 *   2. sibling boxes whose rectangles intersect,
 *   3. one-word-per-line squeezes (an action link/label wrapping to > 3 lines).
 *
 * Runs against the live stack when it is up, else against mocked API
 * responses (same pattern as test_06_ui_journey.spec.ts), so it is green
 * in CI and on a plain checkout. Mocked fixtures carry the shapes that
 * matter here: a long spec, 30+ sync runs, a long drift label for the
 * evidence graph, and every repair state.
 */

import { test, expect, type Page } from "@playwright/test";

const UI_BASE = process.env.E2E_UI_BASE || process.env.UI_BASE || "http://localhost:8107";
const CONTROL_BASE = process.env.E2E_CONTROL_BASE || "http://localhost:8101";

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
];

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONN_ID = "conn-layout-001";
const REPAIR_ID = "rep-layout-001";
const DRIFT_ID = "drift-layout-001";

const LONG_SPEC_YAML = [
  "spec_version: 3",
  "connector:",
  "  id: atlaspms",
  "  vendor: AtlasPMS",
  "source:",
  "  base_url: http://localhost:8105",
  "  timezone: Europe/Berlin",
  "  auth: {type: bearer, credential_ref: ATLAS_API_KEY}",
  "operations:",
  ...Array.from({ length: 12 }, (_, i) => [
    `  list_reservations_${i}:`,
    "    method: GET",
    `    path: /api/v1.3/getReservations${i}`,
    "    pagination: {strategy: cursor, cursor_param: cursor, limit_param: limit, next_path: $.next_cursor, items_path: $.data}",
  ]).flat(),
  "entities:",
  ...Array.from({ length: 8 }, (_, i) => [
    `  Reservation${i}:`,
    "    key: $.reservation_id",
    `    from_operation: list_reservations_${i}`,
    "    fields: {reservation_id: {path: $.reservation_id}, total_amount: {path: $.total_amount, transform: money}}",
    "    checks:",
    "      - {kind: money_invariant, field: total_amount, compare_to: $.gross_sum}",
    "      - {kind: unique_key, field: reservation_id}",
  ]).flat(),
].join("\n");

function syncRuns() {
  // 30+ runs sharing one fingerprint, then a drift starting a new group.
  const runs = [];
  const base = Date.now();
  for (let i = 0; i < 32; i++) {
    runs.push({
      id: `sync-layout-${String(i).padStart(2, "0")}`,
      connector_id: CONN_ID,
      outcome: "Complete",
      unmet_criteria: [],
      fingerprint_id: "fp-same-3520bd6c",
      created_at: new Date(base - (32 - i) * 60000).toISOString(),
    });
  }
  runs.push({
    id: "sync-layout-32",
    connector_id: CONN_ID,
    outcome: "Degraded",
    unmet_criteria: ["money_invariant"],
    fingerprint_id: "fp-new-77b78e79",
    created_at: new Date(base).toISOString(),
  });
  return runs;
}

const connectorDetail = {
  id: CONN_ID,
  name: "Atlas PMS with a very long connector name for wrapping",
  base_url: "http://localhost:8105",
  state: "degrading",
  activity: "drift detected 4 s ago · repairing at L1",
  last_sync_at: new Date().toISOString(),
  health_status: "healthy",
  active_release_id: "rel-2",
  credential_ref: "ATLAS_API_KEY",
  target_entities: ["Reservation", "FolioCharge"],
  runtime_target: { kind: "local_process" },
  vendor_docs_ref: "file:AtlasPMS.md",
  spec_yaml: LONG_SPEC_YAML,
  spec: { spec_version: 3, connector: { id: "atlaspms" } },
  releases: [
    {
      id: "rel-2",
      version: 2,
      connector_id: CONN_ID,
      spec: { spec_version: 3 },
      parent_release_id: "rel-1",
      signature: "4a5aa12461c6e8f0b2d4c6a8e0f2a4b6c8d0e2f4a6b8",
      approved_by: "operator",
      created_at: new Date().toISOString(),
    },
    {
      id: "rel-1",
      version: 1,
      connector_id: CONN_ID,
      spec: { spec_version: 3 },
      parent_release_id: null,
      signature: "0705e3b5a1c9d7e2f4b6a8c0d2e4f6a8b0c2d4e6f8a0",
      approved_by: null,
      created_at: new Date().toISOString(),
    },
  ],
  sync_runs: syncRuns(),
  fingerprints: [
    { id: "fp-same-3520bd6c", connector_id: CONN_ID, hash: "3520bd6c039e71e2", created_at: new Date().toISOString() },
    { id: "fp-new-77b78e79", connector_id: CONN_ID, hash: "77b78e79039e71e2", created_at: new Date().toISOString() },
  ],
  dependencies: [],
};

const connectorsList = [
  {
    id: CONN_ID,
    name: "Atlas PMS with a very long connector name for wrapping",
    base_url: "http://localhost:8105",
    health_status: "healthy",
    state: "degrading",
    activity: "drift detected 4 s ago · repairing at L1",
    last_sync_at: new Date().toISOString(),
    active_release_id: "rel-2",
    credential_ref: "ATLAS_API_KEY",
    target_entities: ["Reservation", "FolioCharge"],
    runtime_target: { kind: "local_process" },
    vendor_docs_ref: "file:AtlasPMS.md",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

function repairFixture(state: string) {
  const base = {
    id: REPAIR_ID,
    drift_event_ids: [DRIFT_ID],
    ladder_level_reached: 3,
    vendor: "harborpms",
    drift_class: "SEMANTIC_VALUE_DRIFT",
    drift_signature: "layout-sig-001",
    fingerprint_delta: { semantic_changed: true },
    candidate_patch: [{ op: "replace", path: "/entities/Reservation/checks/3/compare_to", value: "$.gross_sum" }],
    rule_id: null,
    hypothesis_ids: ["hyp-1"],
    visible_test_results: [{ name: "money_invariant", passed: true }],
    verifier_report_id: "vr-layout-1",
    decision: "propose",
    model_usage: { frontier_tokens: 17405, small_tokens: 400, calls: 2 },
    ladder_trace: [
      { level: 0, action: "knowledge lookup", reason: "L0 miss: no known fix for harborpms" },
      { level: 1, action: "no rule matched", reason: "L1: no rule precondition matched SEMANTIC_VALUE_DRIFT" },
      { level: 2, action: "ranked", reason: "L2 ranked 2 hypotheses below threshold" },
      {
        level: 3,
        action: "frontier_generate",
        reason: "L3 proposed a patch (1 ops: /entities/Reservation/checks/3/compare_to)",
        proposals: [
          {
            patch: [{ op: "replace", path: "/entities/Reservation/checks/3/compare_to", value: "$.net_sum" }],
            passed: false,
            error: "`$.net_sum` is not a field in the response; available: `total` (number of records on this page)",
          },
          {
            patch: [{ op: "replace", path: "/entities/Reservation/checks/3/compare_to", value: "$.gross_sum" }],
            passed: true,
            error: "",
          },
        ],
      },
    ],
    escalation_bundle: null,
    approval: null,
    created_at: new Date().toISOString(),
  };
  if (state === "climbing") {
    return {
      ...base,
      decision: "climbing",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_level_reached: 2,
      ladder_trace: [
        { level: 0, action: "knowledge lookup", reason: "L0 miss: no known fix for harborpms" },
        { level: 1, action: "no rule matched", reason: "L1: no rule precondition matched SEMANTIC_VALUE_DRIFT" },
        { level: 2, action: "ranking", reason: "L2 ranking hypotheses", model: "small-model", call_started_at: new Date().toISOString() },
      ],
    };
  }
  if (state === "escalated") {
    return {
      ...base,
      decision: "escalate",
      verifier_report_id: null,
      candidate_patch: [],
      ladder_trace: [
        ...base.ladder_trace.slice(0, 3),
        {
          level: 3,
          action: "model_escalation",
          reason: "L3 asked for a human decision",
          model_question: "Should Reservation.total_amount track the vendor net amount or stay defined as gross?",
          model_rationale: "Observed amounts sum below the gross baseline.",
          proposals: (base.ladder_trace[3] as { proposals: unknown }).proposals,
          dry_run: "tested on the current records: the checks pass",
        },
        { level: 4, action: "escalate", reason: "L3 asked for a human decision (SEMANTIC_VALUE_DRIFT)" },
      ],
      escalation_bundle: {
        reason: "SEMANTIC_VALUE_DRIFT: the engine is not confident",
        question: "Should Reservation.total_amount track the vendor net amount or stay defined as gross?",
        suggested_action: "answer the question",
        proposal: {
          label: "Accept net amounts as the new gross total for this vendor",
          effect: "Repoints the money invariant to the net total the vendor now sends.",
          patch: [{ op: "replace", path: "/entities/Reservation/checks/3/compare_to", value: "$.gross_sum" }],
          dry_run: "tested on the current records: the checks pass",
          field_notes: [{ path: "/entities/Reservation/checks/3/compare_to", note: "`$.gross_sum` is `1000.00` in the current response (a money sum)" }],
        },
      },
    };
  }
  if (state === "approved") {
    return {
      ...base,
      decision: "approved",
      approval: {
        id: "ap-layout-1",
        actor: "operator",
        decision: "approve",
        from_release_id: "rel-1",
        to_release_id: "rel-2",
        created_at: new Date().toISOString(),
      },
    };
  }
  return base;
}

function ledgerFixture(state: string) {
  const calls =
    state === "escalated"
      ? [
          { tier: "frontier", purpose: "l3-generate-patch", model: "muse-spark-1.3", mode: "live", outcome: "rejected by dry-run", input_tokens: 8000, output_tokens: 9000, reasoning_tokens: 8000, cost: "0.05" },
          { tier: "frontier", purpose: "l3-generate-patch-retry", model: "muse-spark-1.3", mode: "live", outcome: "passed dry-run", input_tokens: 100, output_tokens: 100, reasoning_tokens: 50, cost: "0.001" },
        ]
      : state === "propose" || state === "approved"
        ? [{ tier: "frontier", purpose: "l3-generate-patch", model: "muse-spark-1.3", mode: "live", outcome: "passed dry-run", input_tokens: 8000, output_tokens: 9000, reasoning_tokens: 8000, cost: "0.05" }]
        : [];
  return {
    repair_run_id: REPAIR_ID,
    decision: state === "propose" ? "propose" : state,
    resolved_rung: 3,
    attempts: [
      { rung: 0, action: "knowledge lookup", reason: "L0 miss", calls: [] as unknown[] },
      { rung: 1, action: "no rule matched", reason: "L1 miss", calls: [] as unknown[] },
      { rung: 2, action: "ranked", reason: "L2 ranked", calls: [] as unknown[] },
      {
        rung: 3,
        action: "frontier_generate",
        reason: "L3 proposed a patch",
        calls,
        proposals: [
          { patch: [{ op: "replace", path: "/entities/Reservation/checks/3/compare_to", value: "$.gross_sum" }], passed: true, error: "" },
        ],
      },
    ],
    total_calls: calls.length,
    small_tokens: 400,
    frontier_tokens: 17405,
    total_cost: "0.05",
    frontier_budget: 131072,
  };
}

// ---------------------------------------------------------------------------
// In-page checker (runs in the browser via page.evaluate)
// ---------------------------------------------------------------------------

async function checkLayout(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const problems: string[] = [];
    const rectOf = (el: Element) => el.getBoundingClientRect();
    // On-screen only: off-screen boxes keep their size but can never
    // overlap anything the operator sees (the repair page is scrolled to
    // the bottom before measuring).
    const visible = (el: Element) => {
      const r = rectOf(el);
      return (
        r.width > 0 &&
        r.height > 0 &&
        r.bottom > 0 &&
        r.top < window.innerHeight &&
        r.right > 0 &&
        r.left < window.innerWidth
      );
    };
    const scrollsX = (el: Element) => {
      const o = getComputedStyle(el).overflowX;
      return o === "auto" || o === "scroll";
    };
    const insideScrollable = (el: Element | null) => {
      let p: Element | null = el;
      while (p) {
        if (scrollsX(p)) return true;
        p = p.parentElement;
      }
      return false;
    };
    const insideSvg = (el: Element | null) => {
      let p: Element | null = el;
      while (p) {
        if (p.tagName.toLowerCase() === "svg") return true;
        p = p.parentElement;
      }
      return false;
    };
    const label = (el: Element) =>
      `<${el.tagName.toLowerCase()}> "${(el.textContent || "").trim().slice(0, 70)}"`;
    // Visually-hidden helpers (screen-reader text, clipped to 1px) never
    // overflow by design.
    const isHiddenHelper = (el: Element) => !!el.closest(".sr-only, [aria-hidden='true']");
    // Content of a closed <details> (other than its own summary row) has
    // no box on screen — even in browsers whose getBoundingClientRect
    // still reports one. Skip it everywhere below.
    const insideClosedDetails = (el: Element) => {
      let p: Element | null = el.parentElement;
      let inSummary = false;
      while (p) {
        const t = p.tagName.toLowerCase();
        if (t === "summary") inSummary = true;
        if (t === "details") {
          if (!(p as HTMLDetailsElement).open && !inSummary) return true;
          inSummary = false;
        }
        p = p.parentElement;
      }
      return false;
    };

    // 1. Text overflowing its box — skip intentional scrollers and ellipsis.
    const textEls = document.querySelectorAll(
      "main td, main th, main p, main span, main a, main h1, main h2, main h3, main li, main dt, main dd, main button, main label, main div.rd-card",
    );
    textEls.forEach((el) => {
      if (!visible(el)) return;
      if (isHiddenHelper(el) || insideClosedDetails(el)) return;
      if (insideScrollable(el) || insideSvg(el)) return;
      const cs = getComputedStyle(el);
      if (cs.textOverflow === "ellipsis" && (cs.overflow === "hidden" || cs.whiteSpace === "nowrap")) return;
      const htmlEl = el as HTMLElement;
      if (htmlEl.scrollWidth > htmlEl.clientWidth + 2) {
        problems.push(`overflow: ${label(el)} scrollWidth=${htmlEl.scrollWidth} clientWidth=${htmlEl.clientWidth}`);
      }
    });

    // 2. Sibling boxes intersecting — ancestor-related pairs are layout, not bugs.
    const boxes = [...document.querySelectorAll("main [data-testid], main .btn, main table, main .rd-card")].filter(
      (el) =>
        visible(el) &&
        !insideSvg(el) &&
        !isHiddenHelper(el) &&
        !insideClosedDetails(el) &&
        getComputedStyle(el).position !== "fixed",
    );
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        if (a.contains(b) || b.contains(a)) continue;
        const ra = rectOf(a);
        const rb = rectOf(b);
        const ix = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const iy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (ix > 2 && iy > 2 && ix * iy > 16) {
          problems.push(`overlap: ${label(a)} <-> ${label(b)} (${Math.round(ix)}x${Math.round(iy)})`);
        }
      }
    }

    // 3. One-word-per-line squeezes on actions/labels: count the text's
    // own wrapped lines (a Range's client rects), so button padding never
    // counts as wrapping.
    const actions = document.querySelectorAll("main a, main button");
    actions.forEach((el) => {
      if (!visible(el) || insideScrollable(el) || isHiddenHelper(el) || insideClosedDetails(el)) return;
      const text = el.textContent || "";
      if (!text.trim()) return;
      const range = document.createRange();
      try {
        range.selectNodeContents(el);
      } catch {
        return;
      }
      const tops = new Set<number>();
      for (const r of [...range.getClientRects()]) {
        if (r.width > 0 && r.height > 0) tops.add(Math.round(r.top));
      }
      const r = rectOf(el);
      if (tops.size > 3 && r.width < 140) {
        problems.push(`squeeze: ${label(el)} ${Math.round(r.width)}x${Math.round(r.height)} in ${tops.size} lines`);
      }
    });
    return problems;
  });
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

function isControlReachable(request: { get: (url: string, opts?: object) => Promise<{ ok: () => boolean }> }) {
  return request
    .get(`${CONTROL_BASE}/healthz`, { timeout: 3000 })
    .then((r) => r.ok())
    .catch(() => false);
}

test.describe("REQ-055 item 3f layout: no overlap or overflow", () => {
  let repairState = "propose";
  // With a live stack the pages render live data (the check still runs);
  // the fixture-content assertions below only apply to the mocked render.
  let mocked = false;

  test.beforeEach(async ({ page, request }) => {
    const controlUp = await isControlReachable(request);
    mocked = !controlUp || process.env.E2E_FORCE_MOCKS === "1";
    if (!mocked) return; // live stack: exercise the real pages.
    await page.route("**/api/**", async (route) => {
      const url = route.request().url();
      const ok = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
      if (url.includes("/repairs/") && url.includes("/ledger")) return ok(ledgerFixture(repairState));
      if (url.includes("/repairs/") && url.includes("/approve-preview"))
        return ok({ repair_run_id: REPAIR_ID, approvable: repairState === "propose" || repairState === "escalated", reason: "", approve_lines: ["Creates release r3 from r2."], reject_lines: ["The drift stays open."], facts: {} });
      if (url.includes("/repairs/") && !url.includes("/approve") && !url.includes("/reject"))
        return ok(repairFixture(repairState));
      if (url.match(/\/repairs\/?(\?|$)/)) return ok([repairFixture(repairState)]);
      if (url.includes("/drift/") && !url.endsWith("/drift")) return ok({ id: DRIFT_ID, connector_id: CONN_ID, drift_class: "SEMANTIC_VALUE_DRIFT", severity: "high", evidence_ids: ["ev-1"], source_sync_run_id: "sync-layout-32", status: "open", created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      if (url.includes("/drift")) return ok([{ id: DRIFT_ID, connector_id: CONN_ID, drift_class: "SEMANTIC_VALUE_DRIFT", severity: "high", evidence_ids: ["ev-1"], source_sync_run_id: "sync-layout-32", status: "open", created_at: new Date().toISOString(), updated_at: new Date().toISOString() }]);
      if (url.includes("/evidence/") && !url.includes("/runs/")) return ok({ id: "ev-1", kind: "observation", payload: { check: "money_invariant", observed: "900.00", baseline: "1000.00" }, parent_ids: [], run_id: "sync-layout-32", created_at: new Date().toISOString() });
      if (url.includes("/evidence")) return ok([{ id: "ev-1", kind: "observation", payload: {}, parent_ids: [], run_id: "sync-layout-32", created_at: new Date().toISOString() }]);
      if (url.includes("/runs/") && url.includes("/evidence"))
        return ok([
          { id: "deriv-1", kind: "hypothesis", payload: { drift_class: "SEMANTIC_VALUE_DRIFT", stages: [{ name: "generated", count: 6, ids: ["h1"] }, { name: "distinct", count: 1, ids: ["h1"] }] }, parent_ids: [], run_id: `repair_runs:${REPAIR_ID}`, created_at: new Date().toISOString() },
          { id: "ev-1", kind: "observation", payload: {}, parent_ids: [], run_id: "sync-layout-32", created_at: new Date().toISOString() },
        ]);
      if (url.includes("/runs/")) return ok({ kind: "sync", payload: { sync_run: { id: "sync-layout-32", release_id: "rel-2", started_at: new Date(Date.now() - 3000).toISOString(), finished_at: new Date().toISOString(), records_observed: { Reservation: 30 }, records_emitted: { Reservation: 27 }, outcome: "Degraded", unmet_criteria: ["money_invariant"] } } });
      if (url.match(/\/connectors\/[^/?]+\/sync-runs/)) return ok(syncRuns());
      // REQ-058: the refused build's facts ride the vocabulary check, so
      // the page can draft the capability request with no model call.
      // (Before the generic connector-detail match: /vocabulary-check
      // would match /connectors/<id>.)
      if (url.includes("/connectors/vocabulary-check"))
        return ok({
          refused: true,
          reason: "operations need POST with a JSON body and body-field auth; not expressible in ConnectorSpec v1",
          vocabulary: "v1",
          suggested_vocabulary: "v2",
          refused_methods: ["POST"],
          refused_protocol: null,
          doc_type: "openapi",
          auth_kind: "body_fields",
          operations: [
            { operation_id: "searchAvailability", method: "POST", path: "/searchAvailabilityWithAVeryLongPathNameForWrapping" },
            { operation_id: "createReservation", method: "POST", path: "/reservations" },
          ],
          record_types: ["Reservation", "Availability"],
          has_body_fields: true,
          has_event_feeds: false,
        });
      if (url.match(/\/connectors\/[^/?]+$/)) return ok(connectorDetail);
      if (url.includes("/connectors")) return ok(connectorsList);
      if (url.includes("/capabilities")) return ok({ transforms: ["money", "enum_map"], versions: { v1: { methods: ["GET"], auth_types: ["api_key"], pagination_strategies: ["page"], protocols: ["http"], transforms: ["money"], checks: ["money_invariant"], joins: false, body_templates: false, event_feeds: false, command_templates: false }, v2: { methods: ["GET", "POST"], auth_types: ["api_key", "bearer"], pagination_strategies: ["page", "cursor"], protocols: ["http"], transforms: ["money"], checks: ["money_invariant"], joins: true, body_templates: true, event_feeds: false, command_templates: false }, v3: { methods: ["GET", "POST"], auth_types: ["edge"], pagination_strategies: ["cursor"], protocols: ["event_feed"], transforms: ["money"], checks: ["money_invariant"], joins: false, body_templates: false, event_feeds: true, command_templates: false } }, rules: [{ id: "page_to_cursor", fixes: "paging", confidence: 0.9 }] });
      if (url.includes("/knowledge")) return ok({ entries: [{ vendor: "atlaspms", drift_class: "PAGINATION_CURSOR", drift_signature: "layout-sig-room-for-a-long-row-that-might-overflow-its-box-0123456789", rule_id: "page_to_cursor", patch: [], learned_from: { run_id: "rep-layout-001", rung: 1, tokens: 0, cost: "0", at: new Date().toISOString(), approval_id: "ap-1", actor: "operator", release_id: "rel-2" }, times_reused: 3, tokens_saved: 1200, cost_saved: "0", approvals: 1 }], connectors: [{ connector_id: CONN_ID, name: "Atlas PMS with a very long connector name for wrapping", vocabulary: "v1", refused_vocabularies: [], builds: 2, confirmed_hypotheses: ["amount:total_amount:gross"], last_built_at: new Date().toISOString() }], counts: {} });
      if (url.includes("/metrics/model-usage")) return ok({ frontier_tokens: 17405, small_tokens: 400, calls: 2, total_cost: "0.05" });
      if (url.includes("/discovery")) return ok([]);
      if (url.includes("/demo/simulators")) return ok({ simulators: [{ id: "sim-atlas", display_name: "AtlasPMS demo simulator with a long display name", docs_source: "AtlasPMS.md", developers_url: "http://localhost:8105/developers" }] });
      if (url.includes("/demo/docs")) return ok({ docs_text: "# AtlasPMS developer docs\n\nGET /reservations returns {data, cursor}." });
      if (url.includes("/operator")) return ok({ name: "e2e-operator" });
      if (url.includes("/healthz") || url.includes("/readyz")) return ok({ status: "ok" });
      return ok({});
    });
  });

  async function expectClean(page: Page, where: string, vp: { width: number; height: number }) {
    await page.setViewportSize(vp);
    // Freeze motion so transforms never fake an overlap; let polls land.
    await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important}" });
    await page.waitForTimeout(1200);
    // Scroll the repair page to the bottom (REQ-055 item 3e: Approve stays pinned).
    if (where.startsWith("/repairs/")) {
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(600);
    }
    const problems = await checkLayout(page);
    if (problems.length && process.env.LAYOUT_DEBUG) {
      const dump = await page.evaluate(() => {
        return [...document.querySelectorAll("main [data-testid]")]
          .map((el) => {
            const r = el.getBoundingClientRect();
            const dt = (el as HTMLElement).dataset?.testid;
            return `${(el as HTMLElement).tagName.toLowerCase()}#${dt} ${Math.round(r.x)},${Math.round(r.y)} ${Math.round(r.width)}x${Math.round(r.height)} display=${getComputedStyle(el).display}`;
          })
          .join("\n");
      });
      console.log(`LAYOUT DUMP ${where} @ ${vp.width}x${vp.height}:\n${dump}`);
    }
    expect(problems, `${where} @ ${vp.width}x${vp.height}`).toEqual([]);
  }

  for (const vp of VIEWPORTS) {
    test(`connectors list clean @ ${vp.width}x${vp.height}`, async ({ page }) => {
      await page.goto(`${UI_BASE}/connectors`);
      // The mock rows render (never an empty error page under the check).
      if (mocked) await expect(page.getByText("Atlas PMS with a very long").first()).toBeVisible();
      await expectClean(page, "/connectors", vp);
    });

    test(`connector page (long spec, 30+ runs) clean @ ${vp.width}x${vp.height}`, async ({ page }) => {
      await page.goto(`${UI_BASE}/connectors/${CONN_ID}`);
      if (mocked) {
        // Fingerprints are grouped and collapsed by default (REQ-057): the
        // summary is visible, entries only after opening + expanding.
        await expect(page.getByTestId("fingerprints-summary")).toBeVisible();
        await expect(page.getByTestId("fingerprint-group-0")).toContainText("32 syncs");
        await page.getByTestId("fingerprints-summary").click();
        await page.getByTestId("fingerprint-group-toggle-0").click();
        await expect(page.getByTestId("fingerprint-entry-sync-layout-00")).toBeVisible();
        // Truncated on screen, full value on hover (REQ-057 item 4).
        await expect(page.locator('[data-testid="fingerprint-group-0"] code').first()).toHaveAttribute(
          "title",
          "fp-same-3520bd6c",
        );
        // The clicks above scrolled the page; measure from the top like a
        // reviewer sees on load (the tall sync-runs table's unclipped rect
        // would otherwise paint false overlaps mid-page).
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(400);
      }
      await expectClean(page, `/connectors/${CONN_ID}`, vp);
    });

    test(`build page clean @ ${vp.width}x${vp.height}`, async ({ page }) => {
      await page.goto(`${UI_BASE}/connectors/new`);
      if (mocked) {
        // Picking a simulator loads its portal docs into the form.
        await page.getByTestId("input-sim-docs").selectOption("sim-atlas");
        await expect(page.getByTestId("input-docs-text")).toHaveValue(/# AtlasPMS developer docs/);
      }
      await expectClean(page, "/connectors/new", vp);
    });

    test(`build page refused (capability request) clean @ ${vp.width}x${vp.height}`, async ({ page }) => {
      await page.goto(`${UI_BASE}/connectors/new`);
      if (mocked) {
        // The vocabulary check refuses (mocked above): the refused box
        // with "Build with v2" + "Download capability request" renders.
        await page.getByTestId("input-name").fill("MeridianPMS with a very long vendor name for wrapping");
        await page.getByTestId("input-base-url").fill("http://localhost:8116");
        await page.getByTestId("input-docs-text").fill("# Meridian POST API");
        await page.getByTestId("submit-build").click();
        await expect(page.getByTestId("vocabulary-refused")).toBeVisible();
        await expect(page.getByTestId("download-capability-request")).toBeVisible();
      }
      await expectClean(page, "/connectors/new [refused]", vp);
    });

    test(`knowledge page clean @ ${vp.width}x${vp.height}`, async ({ page }) => {
      await page.goto(`${UI_BASE}/knowledge`);
      if (mocked) await expect(page.getByText("atlaspms / PAGINATION_CURSOR").first()).toBeVisible();
      await expectClean(page, "/knowledge", vp);
    });

    const stateText: Record<string, string> = {
      propose: "Approve release r3",
      climbing: "Repairing at",
      escalated: "Should Reservation.total_amount track the vendor net amount",
      approved: "Approved by operator",
    };
    for (const state of ["propose", "climbing", "escalated", "approved"]) {
      test(`repair page (${state}) clean @ ${vp.width}x${vp.height}`, async ({ page }) => {
        repairState = state;
        await page.goto(`${UI_BASE}/repairs/${REPAIR_ID}`);
        if (mocked) await expect(page.getByText(stateText[state]).first()).toBeVisible();
        await expectClean(page, `/repairs/${REPAIR_ID} [${state}]`, vp);
      });
    }
  }
});
