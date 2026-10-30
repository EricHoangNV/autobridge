/**
 * P2.S6.1 — Richer UI acceptance (plan §5 S6).
 *
 * No new API surface: the two views under test consume only existing
 * endpoints (`/repairs/{id}`, `/drift/{id}`, `/evidence/{id}`,
 * `GET /metrics/model-usage`). This spec:
 *
 * 1. Loads `/repairs/{id}` (live stack when up, mocked API routes when not)
 *    and asserts the per-run model-usage chart and the evidence graph render.
 * 2. Captures Playwright screenshots of both views into `test-results/` and
 *    asserts the files are written and non-empty.
 *
 * Usage:
 *   npx playwright test tests/e2e/test_s6_richer_ui.spec.ts   # from repo root
 *   E2E_UI_BASE=http://localhost:3000 npx playwright test tests/e2e/test_s6_richer_ui.spec.ts
 */

import { test, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

const UI_BASE = process.env.E2E_UI_BASE || process.env.UI_BASE || "http://localhost:8107";
const CONTROL_BASE = process.env.E2E_CONTROL_BASE || "http://localhost:8101";

// ---------------------------------------------------------------------------
// Mock fixtures — deterministic data so the chart/graph/screenshots are
// stable with or without the stack.
// ---------------------------------------------------------------------------

const MOCK_REPAIR_ID = "rep-s6-001";
const MOCK_DRIFT_ID = "drift-s6-001";
const MOCK_CONNECTOR_ID = "conn-s6-001";

const mockRepair = {
  id: MOCK_REPAIR_ID,
  drift_event_ids: [MOCK_DRIFT_ID],
  ladder_level_reached: 2,
  candidate_patch: [{ op: "replace", path: "/operations/list/pagination/strategy", value: "cursor" }],
  rule_id: "offset_to_cursor",
  hypothesis_ids: ["ev-s6-hyp"],
  visible_test_results: [{ name: "offset_to_cursor", passed: true }],
  verifier_report_id: "vr-s6-001",
  decision: "propose",
  model_usage: { frontier_tokens: 0, small_tokens: 340, frontier_calls: 0, small_calls: 3 },
  escalation_bundle: null,
  created_at: new Date().toISOString(),
};

const mockDrift = {
  id: MOCK_DRIFT_ID,
  connector_id: MOCK_CONNECTOR_ID,
  drift_class: "PAGINATION_CURSOR",
  severity: "medium",
  evidence_ids: ["ev-s6-probe", "ev-s6-hyp"],
  source_sync_run_id: "sync-s6-2",
  status: "open",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const mockProbe = {
  id: "ev-s6-probe",
  kind: "probe",
  payload: { url: "/reservations?limit=2", status: 200 },
  parent_ids: [],
  run_id: "run-s6-a",
  created_at: new Date().toISOString(),
};

const mockHypothesis = {
  id: "ev-s6-hyp",
  kind: "hypothesis",
  payload: { hypothesis: "cursor pagination needed", confidence: 0.9 },
  parent_ids: ["ev-s6-probe"],
  run_id: "run-s6-a",
  created_at: new Date().toISOString(),
};

const mockModelUsage = {
  total_calls: 5,
  total_input_tokens: 1200,
  total_output_tokens: 600,
  total_cost: "0.02",
  frontier_tokens: 900,
  small_tokens: 900,
  by_tier: { frontier: 900, small: 900 },
  by_provider: { anthropic: 4, openai: 1 },
  by_purpose: { rank: 3, patch: 2 },
  entries: [
    {
      provider: "anthropic",
      model: "claude-haiku-4-5",
      tier: "small",
      total_calls: 4,
      total_input_tokens: 700,
      total_output_tokens: 200,
      total_cost: "0.008",
      by_run: [
        { run_id: "run-s6-a", purpose: "rank", tokens: 600 },
        { run_id: "run-s6-b", purpose: "rank", tokens: 300 },
      ],
    },
    {
      provider: "anthropic",
      model: "claude-opus-4-6",
      tier: "frontier",
      total_calls: 1,
      total_input_tokens: 500,
      total_output_tokens: 400,
      total_cost: "0.012",
      by_run: [{ run_id: "run-s6-a", purpose: "patch", tokens: 900 }],
    },
  ],
};

async function isUiReachable(request: any): Promise<boolean> {
  try {
    const res = await request.get(`${UI_BASE}/healthz`, { timeout: 3000 });
    return res.ok();
  } catch {
    return false;
  }
}

function isControlReachable(request: any): Promise<boolean> {
  return request
    .get(`${CONTROL_BASE}/healthz`, { timeout: 3000 })
    .then((r: any) => r.ok())
    .catch(() => false);
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe("P2.S6.1 richer UI — per-run chart + evidence graph + screenshots", () => {
  test.beforeEach(async ({ page, request }) => {
    const controlUp = await isControlReachable(request);
    if (controlUp) return; // live stack: assert against real data

    const mockGet = async (route: any) => {
      const url = route.request().url();
      if (url.match(/\/repairs\/[^/]+/)) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockRepair) });
      }
      if (url.match(/\/drift\/[^/]+/)) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockDrift) });
      }
      if (url.includes("/metrics/model-usage")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockModelUsage) });
      }
      if (url.match(/\/evidence\/ev-s6-probe/)) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockProbe) });
      }
      if (url.match(/\/evidence\/ev-s6-hyp/)) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockHypothesis) });
      }
      if (url.match(/\/evidence\/[^/]+/)) {
        return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
      }
      if (url.includes("/operator")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ name: "e2e-operator" }) });
      }
      if (url.includes("/metrics/sync-outcomes")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total: 3, by_outcome: { Complete: 3 } }) });
      }
      if (url.includes("/healthz") || url.includes("/readyz")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok", checks: { ui: "ok" } }) });
      }
      return route.continue();
    };

    await page.route("**/api/**", (route) =>
      route.request().method() === "GET" ? mockGet(route) : route.continue(),
    );
    await page.route("**/localhost:8101/**", (route) =>
      route.request().method() === "GET" ? mockGet(route) : route.continue(),
    );
    await page.route("**/control:8000/**", (route) =>
      route.request().method() === "GET" ? mockGet(route) : route.continue(),
    );
  });

  async function gotoRepair(page: any, request: any): Promise<boolean> {
    const uiReachable = await isUiReachable(request);
    const controlUp = await isControlReachable(request);
    // Live stack with no repairs has nothing to render — skip (mocked mode
    // covers the chart/graph with fixtures). Discover a real repair id first.
    if (uiReachable && controlUp) {
      let liveRepairId: string | null = null;
      try {
        const res = await request.get(`${CONTROL_BASE}/repairs`, { timeout: 3000 });
        if (res.ok()) {
          const list = await res.json();
          if (Array.isArray(list) && list.length > 0) liveRepairId = list[0].id;
        }
      } catch {}
      if (!liveRepairId) {
        test.skip(true, "no repairs on the live stack — run `make demo` first");
        return false;
      }
      await page.goto(`${UI_BASE}/repairs/${liveRepairId}`, { waitUntil: "domcontentloaded", timeout: 10000 });
      await expect(page.getByTestId("repair-detail-page")).toBeVisible({ timeout: 15000 });
      return true;
    }
    try {
      await page.goto(`${UI_BASE}/repairs/${MOCK_REPAIR_ID}`, { waitUntil: "domcontentloaded", timeout: 10000 });
    } catch {
      test.skip(true, "UI not reachable — run `make up` or `npm run dev` in ui/");
      return false;
    }
    if (!uiReachable) {
      // Mocked mode still needs the Next.js server; the goto above proves it.
    }
    await expect(page.getByTestId("repair-detail-page")).toBeVisible({ timeout: 15000 });
    return true;
  }

  test("per-run model-usage chart renders stacked frontier/small rows", async ({ page, request }) => {
    if (!(await gotoRepair(page, request))) return;
    // Live stack: assert live-shaped data (run ids are real repair ids, and
    // a repair with no model calls shows the empty state, not rows).
    if (await isControlReachable(request)) {
      const chart = page.getByTestId("model-usage-chart");
      const empty = page.getByTestId("model-usage-chart-empty");
      await expect(chart.or(empty)).toBeVisible({ timeout: 15000 });
      if (await chart.isVisible()) {
        const rows = page.getByTestId("model-usage-chart-row");
        await expect(rows.first()).toBeVisible({ timeout: 15000 });
        const count = await rows.count();
        expect(count).toBeGreaterThan(0);
        for (let i = 0; i < count; i++) {
          await expect(rows.nth(i)).toHaveAttribute("data-run-id", /.+/);
        }
      }
      return;
    }
    const chart = page.getByTestId("model-usage-chart");
    await expect(chart).toBeVisible({ timeout: 15000 });

    const rows = page.getByTestId("model-usage-chart-row");
    await expect(rows).toHaveCount(2, { timeout: 15000 });
    // run-s6-a carries both tiers (stacked), run-s6-b small only
    await expect(rows.first()).toHaveAttribute("data-run-id", "run-s6-a");
    const frontierSegs = page.getByTestId("model-usage-chart-row-frontier");
    await expect(frontierSegs).toHaveCount(1);
    await expect(page.getByTestId("model-usage-chart-row-small")).toHaveCount(2);
  });

  test("evidence graph renders drift, evidence and parent-link edges", async ({ page, request }) => {
    if (!(await gotoRepair(page, request))) return;
    // Live stack: node ids are real drift/evidence ids — assert structure.
    if (await isControlReachable(request)) {
      const graph = page.getByTestId("evidence-graph");
      await expect(graph).toBeVisible({ timeout: 15000 });
      const nodes = page.locator('[data-testid^="evidence-graph-node-"]');
      await expect(nodes.first()).toBeVisible({ timeout: 15000 });
      expect(await nodes.count()).toBeGreaterThan(0);
      return;
    }
    const graph = page.getByTestId("evidence-graph");
    await expect(graph).toBeVisible({ timeout: 15000 });

    await expect(page.getByTestId("evidence-graph-node-drift-s6-001")).toBeVisible();
    await expect(page.getByTestId("evidence-graph-node-ev-s6-probe")).toBeVisible();
    await expect(page.getByTestId("evidence-graph-node-ev-s6-hyp")).toBeVisible();
    const edges = page.getByTestId("evidence-graph-edge");
    // drift->probe, drift->hypothesis, probe->hypothesis (parent link)
    await expect(edges).toHaveCount(3);
  });

  test("screenshots: chart + graph captured to test-results", async ({ page, request }) => {
    if (!(await gotoRepair(page, request))) return;
    const graph = page.getByTestId("evidence-graph");
    await expect(graph).toBeVisible({ timeout: 15000 });
    const chart = page.getByTestId("model-usage-chart");
    const chartEmpty = page.getByTestId("model-usage-chart-empty");
    await expect(chart.or(chartEmpty)).toBeVisible({ timeout: 15000 });

    const outDir = path.join("test-results", "s6");
    fs.mkdirSync(outDir, { recursive: true });
    const chartShot = path.join(outDir, "s6-model-usage-chart.png");
    const graphShot = path.join(outDir, "s6-evidence-graph.png");
    const pageShot = path.join(outDir, "s6-repair-page.png");

    const chartTarget = (await chart.isVisible()) ? chart : chartEmpty;
    await chartTarget.screenshot({ path: chartShot });
    await graph.screenshot({ path: graphShot });
    await page.screenshot({ path: pageShot, fullPage: true });

    for (const f of [chartShot, graphShot, pageShot]) {
      expect(fs.existsSync(f), `screenshot ${f} written`).toBeTruthy();
      expect(fs.statSync(f).size, `screenshot ${f} non-empty`).toBeGreaterThan(1000);
    }
  });
});
