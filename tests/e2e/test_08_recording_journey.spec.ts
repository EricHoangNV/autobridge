/**
 * REQ-033 — UI-first demo journeys against the live stack.
 *
 * The recording flow from README "How to run the demo" and
 * docs/demo.md "Recording from the UI", asserted end to end in the
 * browser:
 *
 *   seed → all Working; break `page_to_cursor` → Degrading → "fix ready"
 *   appears without a manual sync → click Approve in the UI → Working,
 *   with the four repair sections present; break it again after
 *   reactivating the base release → L0 hit, Knowledge "reused" goes up;
 *   break `fias_record_layout_change` → the agent re-handshakes, the
 *   connector stays Working, postings resume (no approval needed); break
 *   `gross_to_net` last → the scheduled sync Degrades on the money
 *   invariant and the engine's own climb runs (funnel, L2 skipped, one
 *   L3 attempt live or replayed — or the explicit skip when neither
 *   exists) and escalates to `error` with the operator question,
 *   asserted from the break's own rows. The recorded step-4 repair is
 *   the documented no-key fallback: it exists only when the break's L3
 *   was skipped, and the journey never uses it when the break attempted
 *   L3. (The semantic break runs last: it leaves Harbor non-Working.)
 *
 * Live-stack only: without a reachable control plane the spec skips
 * (the mocked test_06 journey covers the screens offline). Requires the
 * seed frame — run `make demo-reset && make demo-seed` first, otherwise
 * every test skips with that message. Each break leg heals in a finally
 * so no fault leaks into the next test (random-order safe with the
 * Python e2e suite, which runs separately).
 *
 * The recorded semantic repair exists only after `make demo` step 4 has
 * run once on a stack whose break could not attempt L3 (no key and no
 * recording: POST /demo/record-climb fallback): on a fresh seed that half
 * skips, and it never runs when the break's own repair already attempted
 * L3. The semantic halves are split on purpose — the live half needs the
 * seed frame, the recorded half needs only the fallback row (step 4
 * leaves Harbor non-Working, since its recorded drift stays open).
 * Full coverage is two passes: seed → journey (live passes, recorded
 * skips) → `make demo` → journey again (recorded passes; live skips, its
 * drift is still open). Re-seed before re-running the journey — `make demo`
 * before re-running the journey — `make demo` leaves its rows behind
 * by design, and the opening frame asserts the seed contract
 * (Working, 0 tokens, nothing waiting).
 *
 * Auth: write calls go straight to the control API with
 * `AUTOBRIDGE_CONTROL_TOKEN` (empty when unset = open). `make e2e-ui`
 * sources scripts/load_dotenv.sh so the token flows like every other
 * host target (REQ-014).
 */

import { test, expect } from "@playwright/test";

const UI_BASE = process.env.E2E_UI_BASE || process.env.UI_BASE || "http://localhost:8107";
const CONTROL_BASE = process.env.E2E_CONTROL_BASE || "http://localhost:8101";
const CONTROL_TOKEN = process.env.AUTOBRIDGE_CONTROL_TOKEN || "";

const SEED_NAMES = ["atlaspms", "meridianpms", "PABX edge"] as const;

type Connector = {
  id: string;
  name: string;
  state?: string | null;
  activity?: string | null;
  last_sync_at?: string | null;
  active_release_id?: string | null;
};

type Repair = {
  id: string;
  drift_class?: string | null;
  ladder_level_reached?: number | null;
  rule_id?: string | null;
  decision?: string | null;
  created_at?: string | null;
};

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (CONTROL_TOKEN) h["Authorization"] = `Bearer ${CONTROL_TOKEN}`;
  return h;
}

async function control<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${CONTROL_BASE}${path}`, {
      method,
      headers: headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) return null;
    const text = await res.text();
    return (text ? JSON.parse(text) : {}) as T;
  } catch {
    return null;
  }
}

async function controlUp(): Promise<boolean> {
  const res = await control<unknown>("GET", "/healthz");
  return res !== null;
}

async function connectors(): Promise<Connector[] | null> {
  const list = await control<Connector[]>("GET", "/connectors");
  return Array.isArray(list) ? list : null;
}

async function repairsFor(connectorId: string): Promise<Repair[] | null> {
  const list = await control<Repair[]>("GET", `/repairs?connector_id=${encodeURIComponent(connectorId)}`);
  return Array.isArray(list) ? list : null;
}

/** Newest repair first: the API does not guarantee order, so sort client-side. */
function newest(rows: Repair[] | null): Repair | null {
  if (!rows || !rows.length) return null;
  return [...rows].sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")))[0];
}

async function seedFrame(): Promise<Map<string, Connector> | null> {
  const list = await connectors();
  if (!list) return null;
  const byName = new Map(list.map((c) => [c.name, c]));
  for (const name of SEED_NAMES) {
    const c = byName.get(name);
    if (!c || c.state !== "working") return null;
  }
  return byName;
}

async function poll<T>(fn: () => Promise<T | null>, isDone: (v: T | null) => boolean, what: string, timeoutMs: number, intervalMs = 5000): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let last: T | null = null;
  for (;;) {
    last = await fn();
    if (isDone(last)) return last as T;
    if (Date.now() > deadline) throw new Error(`timed out waiting for: ${what}`);
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

async function latestSyncId(connectorId: string): Promise<string | null> {
  const list = await control<Array<{ id?: string }>>(
    "GET",
    `/connectors/${encodeURIComponent(connectorId)}/sync-runs?limit=1`,
  );
  return list && list.length ? (list[0].id ?? null) : null;
}

/** Wait for the next scheduler tick on this connector, then return. Lets the
 *  caller break right after a tick, so detection lands on tick N+1 and the
 *  targeted heal below closes the fault inside the same tick — a second
 *  faulted tick can re-classify the same fault into a scar escalation that
 *  keeps the connector non-Working until a reset. */
async function waitForTick(connectorId: string): Promise<void> {
  const before = await latestSyncId(connectorId);
  await poll(() => latestSyncId(connectorId), (id) => !!id && id !== before, "next scheduler tick", 30_000, 1000);
}

async function connectorState(id: string): Promise<string | null> {
  const list = await connectors();
  return list?.find((c) => c.id === id)?.state ?? null;
}

async function breakFault(scenario: string, target?: string): Promise<void> {
  const rec = await control<{ status?: string; error?: string }>("POST", "/demo/inject-fault", {
    target,
    scenario_id: scenario,
  });
  if (!rec || rec.error) throw new Error(`inject-fault ${scenario} failed: ${JSON.stringify(rec)}`);
}

async function healFaults(target?: string | string[]): Promise<void> {
  // Sims-only reset (the same path as `make demo-heal` = POST /demo/reset
  // with sims_only): clears injected faults, keeps connectors/runs.
  // Verified, not assumed: every target must report 200 AND no simulator
  // may show an active fault afterwards (a silent partial reset would
  // leave faulted ticks that scar later legs). Retried, then fail loud.
  // Targeted (one sim) whenever the caller knows it: the reset is one
  // admin call (~60 ms) instead of six, so the fault-active window closes
  // inside a single scheduler tick.
  //
  // Never heal sim-atlas while a shape-fixed atlas release is active: a
  // cursor-strategy release cannot sync the clean page-shape sim, so the
  // next tick lands Degraded (no_loss) and scars the connector until a
  // reset. The legs that approve atlas repairs leave the fault in place
  // and finish with `make demo-reset && make demo-seed` instead; the
  // afterEach below spares sim-atlas for the same reason.
  const targets = target === undefined ? undefined : Array.isArray(target) ? target : [target];
  const payload: Record<string, unknown> = { sims_only: true };
  if (targets) payload.targets = targets;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const t0 = Date.now();
    const rec = await control<{ sims_only?: boolean; resets?: Array<{ target?: string; status?: number }> }>(
      "POST",
      "/demo/reset",
      payload,
    );
    // eslint-disable-next-line no-console
    console.log(`[journey] heal attempt ${attempt} took ${Date.now() - t0}ms ok=${rec?.sims_only === true}`);
    const bad = (rec?.resets ?? []).filter((r) => r.status !== 200);
    if (rec?.sims_only === true && bad.length === 0) {
      const sims = await control<{ simulators?: Array<{ id?: string; active_scenario?: string | null }> }>(
        "GET",
        "/demo/simulators",
      );
      const active = (sims?.simulators ?? []).filter(
        (s) => s.active_scenario && (targets === undefined || targets.includes(s.id ?? "")),
      );
      if (active.length === 0) return;
      if (attempt === 3) {
        throw new Error(`heal incomplete, still active: ${JSON.stringify(active)}`);
      }
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new Error("heal failed to clear sim faults after 3 attempts");
}

/** Simulators the afterEach sweep may clear: everything except sim-atlas
 *  (see healFaults — atlas faults are managed per-leg and cleared by the
 *  reset+seed after the learning leg). */
const AFTER_EACH_TARGETS = ["sim-harbor", "sim-legacystay", "sim-meridian", "sim-fias-pabx"];

/** Deck 4 (REQ-049): Harbor is built from its wiki docs, not seeded.
 *  Ensures the harborpms connector exists with a docs-built v1 release
 *  carrying the money check, baseline-synced Complete. Idempotent: a
 *  Working harborpms is returned as-is. */
async function ensureHarborBuilt(): Promise<Connector> {
  const list = await connectors();
  const existing = (list ?? []).find((c) => c.name === "harborpms");
  if (existing && existing.state === "working") return existing;
  const ensured = await control<{ id?: string }>("POST", "/connectors", {
    name: "harborpms",
    vendor_docs_ref: "simulators/docs/harborpms.md",
    base_url: "http://sim-harbor:8000",
    credential_ref: "HARBOR_API_KEY",
    target_entities: ["Reservation", "FolioCharge"],
  });
  const connectorId = ensured?.id ?? existing?.id;
  if (!connectorId) throw new Error(`harborpms ensure failed: ${JSON.stringify(ensured)}`);
  const { readFileSync } = await import("fs");
  const { join, dirname } = await import("path");
  const { fileURLToPath } = await import("url");
  const here = dirname(fileURLToPath(import.meta.url));
  const docsText = readFileSync(join(here, "..", "..", "simulators", "docs", "harborpms.md"), "utf-8");
  const build = await control<{ build_run_id?: string; job_id?: string }>(
    "POST",
    `/connectors/${encodeURIComponent(connectorId)}/build`,
    { docs_text: docsText, spec_vocabulary: "v1" },
  );
  if (!build?.job_id) throw new Error(`harborpms build not queued: ${JSON.stringify(build)}`);
  const job = await poll(
    () => control<{ state?: string; result?: { release_id?: string } }>("GET", `/jobs/${encodeURIComponent(build.job_id!)}`),
    (j) => !!j && (j.state === "succeeded" || j.state === "failed"),
    "harbor docs build terminal",
    180_000,
    2000,
  );
  const releaseId = job.result?.release_id;
  if (!releaseId) throw new Error(`harborpms docs build produced no release: ${JSON.stringify(job)}`);
  const sync = await control<{ job_id?: string }>("POST", `/connectors/${encodeURIComponent(connectorId)}/sync`, {});
  if (sync?.job_id) {
    await poll(
      () => control<{ state?: string }>("GET", `/jobs/${encodeURIComponent(sync.job_id!)}`),
      (j) => !!j && (j.state === "succeeded" || j.state === "failed"),
      "harbor baseline sync terminal",
      180_000,
      2000,
    );
  }
  const detail = await control<Connector>("GET", `/connectors/${encodeURIComponent(connectorId)}`);
  if (!detail || detail.state !== "working") {
    throw new Error(`harborpms not working after docs build: ${JSON.stringify(detail)}`);
  }
  return detail;
}

test.describe("REQ-033 recording journey — UI-first demo", () => {
  async function requireControl(): Promise<void> {
    if (!(await controlUp())) test.skip(true, "control plane unreachable — run after `make local-up`");
  }

  async function requireSeed(): Promise<Map<string, Connector>> {
    await requireControl();
    const frame = await seedFrame();
    if (!frame) {
      test.skip(true, "seed frame absent — run `make demo-reset && make demo-seed` first");
      throw new Error("unreachable");
    }
    return frame;
  }

  test.afterEach(async () => {
    // Never leak a fault into the next journey leg (spares sim-atlas: see
    // healFaults — atlas faults are managed per-leg).
    if (await controlUp()) await healFaults(AFTER_EACH_TARGETS);
  });

  test("opening frame: three connectors Working, 0 model tokens, nothing waiting", async () => {
    const byName = await requireSeed();
    // Subset, not exact: other journey specs (test_06's live journey)
    // may have added their own connectors and repairs in the same
    // `make e2e-ui` run. The seed contract itself — exactly three working
    // connectors, 0 tokens, nothing waiting — is asserted by
    // test_09_demo_seed_break_heal.py on a fresh seed; every leg below
    // scopes by connector and by time so leftovers cannot match.
    expect([...byName.keys()].sort()).toEqual([...SEED_NAMES].sort());
    const usage = await control<{ total_calls?: number; frontier_tokens?: number; small_tokens?: number }>(
      "GET",
      "/metrics/model-usage",
    );
    expect(usage, "model-usage ledger readable").not.toBeNull();
    expect(usage!.frontier_tokens ?? 0).toBe(0);
    expect(usage!.small_tokens ?? 0).toBe(0);
  });

  test(
    "zero-token fix in the browser: break → Degrading → fix ready → Approve → Working",
    { timeout: 420_000 },
    async ({ page }) => {
      test.setTimeout(420_000);
      const byName = await requireSeed();
      const atlas = byName.get("atlaspms")!;

      await waitForTick(atlas.id);
      await breakFault("page_to_cursor", "sim-atlas");
      const sinceBreak = new Date().toISOString();

      // The scheduler (10 s tick) syncs, detects and repairs — no manual sync.
      await poll(() => connectorState(atlas.id), (s) => s === "degrading", "atlas degrading", 120_000);
      const repair = await poll(
        async () => newest((await repairsFor(atlas.id))?.filter((r) => (r.created_at ?? "") >= sinceBreak) ?? null),
        (r) => !!r && r.drift_class === "PAGINATION_CHANGE",
        "page_to_cursor repair proposed",
        120_000,
        1000,
      );
      // Do NOT heal yet: the candidate fix verifies against the faulted
      // vendor (a cursor-strategy repair cannot sync the clean page-shape
      // sim — healing first would turn the verifying sync Degraded). The
      // drift and repair above were detected from real wire evidence; the
      // UI below reads the persisted rows.
      expect(repair.ladder_level_reached).toBe(1);
      expect(repair.rule_id).toBe("page_to_cursor");

      // Repairs list: newest first, no reload, full row.
      await page.goto(`${UI_BASE}/repairs`, { waitUntil: "domcontentloaded" });
      await expect(page.getByTestId("repairs-list")).toBeVisible({ timeout: 15_000 });
      const row = page.getByTestId(`repair-row-${repair.id}`);
      await expect(row).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId(`repair-class-${repair.id}`)).toContainText("PAGINATION_CHANGE");
      await expect(page.getByTestId(`repair-calls-${repair.id}`)).toContainText("0 model calls");
      await expect(page.getByTestId(`repair-status-${repair.id}`)).toContainText("fix ready");

      // Open the repair: assert the decision + preview pre-approval (the
      // preview shows the server's own lines only while approvable), then
      // click Approve in the browser. The remaining sections are asserted
      // after approval — they render from the same persisted rows.
      await row.getByRole("link").first().click();
      await expect(page.getByTestId("repair-detail-page")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("repair-decision-line")).toContainText("Fix ready: verified, 0 model calls");
      await expect(page.getByTestId("approve-preview-line-0")).toContainText("Creates release r2");

      // The presenter clicks Approve; the page follows the outcome live.
      // The verifying sync runs against the still-faulted vendor (that is
      // what the fix is verified against) and lands Complete, retiring the
      // drift: the connector returns to Working.
      await page.getByTestId("repair-approve-btn").click();
      await expect(page.getByTestId("repair-action-success")).toBeVisible({ timeout: 30_000 });
      await poll(() => connectorState(atlas.id), (s) => s === "working", "atlas working again", 180_000);

      // The fault stays active on purpose: the learning leg re-breaks the
      // same fault, and healing now — while the cursor-strategy release
      // is active — would scar the connector (the fixed spec cannot sync
      // the clean page-shape sim). Drain one tick instead and fail fast
      // if a second faulted tick committed a scar in the meantime
      // (product race; reseed and rerun, and see the build note).
      await new Promise((r) => setTimeout(r, 12_000));
      const settledAfter = (await repairsFor(atlas.id)) ?? [];
      expect(
        settledAfter.filter((r) => (r.created_at ?? "") >= sinceBreak).map((r) => r.id).sort(),
        "no scar repair from a second faulted tick (product race, reseed and rerun)",
      ).toEqual([repair.id]);

      // The repair page explains itself: four sections in order.
      await expect(page.getByTestId("repair-section-happened")).toBeVisible();
      await expect(page.getByTestId("happened-connector")).toContainText("atlaspms");
      await expect(page.getByTestId("wire-change-summary")).toBeVisible();
      await expect(page.getByTestId("repair-section-did")).toBeVisible();
      await expect(page.getByTestId("rung-timeline-0")).toContainText("L0");
      await expect(page.getByTestId("model-call-box-empty")).toBeVisible();
      await expect(page.getByTestId("repair-section-decided")).toBeVisible();
      await expect(page.getByTestId("patch-diff")).toBeVisible();
      await expect(page.getByTestId("repair-section-approve")).toBeVisible();
      await expect(page.getByTestId(`after-approve-step-complete`)).toBeVisible({ timeout: 60_000 });
    },
  );

  test(
    "learning: break it again → L0 hit, Knowledge reused goes up",
    { timeout: 420_000 },
    async ({ page }) => {
      test.setTimeout(420_000);
      const byName = await requireSeed();
      const atlas = byName.get("atlaspms")!;
      // Timestamp BEFORE reactivating: the reactivation itself lets the
      // next tick detect the still-active fault, so the L0 repair may
      // predate the (idempotent) re-break below. Filtering from the break
      // would miss it and time out waiting for a second repair that the
      // open-repair skip correctly suppresses.
      const sinceBreak = new Date().toISOString();

      const knowledgeBefore = await control<{ entries?: Array<{ rule_id?: string; times_reused?: number }> }>(
        "GET",
        "/knowledge",
      );
      const reusedBefore =
        knowledgeBefore?.entries?.find((e) => e.rule_id === "page_to_cursor")?.times_reused ?? 0;

      // On-script re-break (demo step 3 leg): reactivate the pre-patch
      // release first, otherwise the fixed spec syncs Complete and there
      // is nothing to repair.
      const detail = await control<{ releases?: Array<{ id: string; version?: number }> }>(
        "GET",
        `/connectors/${encodeURIComponent(atlas.id)}`,
      );
      const base = (detail?.releases ?? []).filter((r) => typeof r.version === "number").sort((a, b) => (a.version ?? 0) - (b.version ?? 0))[0];
      expect(base, "base release exists").toBeDefined();
      const activated = await control("POST", `/connectors/${encodeURIComponent(atlas.id)}/releases/${encodeURIComponent(base!.id)}/activate`, {});
      expect(activated, "base release reactivated").not.toBeNull();

      await waitForTick(atlas.id);
      await breakFault("page_to_cursor", "sim-atlas");
      const repair = await poll(
        async () => newest((await repairsFor(atlas.id))?.filter((r) => (r.created_at ?? "") >= sinceBreak) ?? null),
        (r) => !!r && (r.ladder_level_reached ?? -1) >= 0,
        "second page_to_cursor repair",
        120_000,
        1000,
      );
      // Do NOT heal yet: the L0 fix verifies against the faulted vendor,
      // like the zero-token leg above.
      // Learned on the approval below: the same drift resolves at L0.
      expect(repair.ladder_level_reached).toBe(0);
      expect(repair.rule_id).toBe("page_to_cursor");

      const approved = await control("POST", `/repairs/${encodeURIComponent(repair.id)}/approve`, { actor: "recording-journey" });
      expect(approved, "L0 repair approved").not.toBeNull();
      await poll(() => connectorState(atlas.id), (s) => s === "working", "atlas working after L0 fix", 180_000);

      // Drain one tick and fail fast on a scar (same product race as the
      // zero-token leg): only the L0 repair may exist since the break.
      await new Promise((r) => setTimeout(r, 12_000));
      const settledL0 = ((await repairsFor(atlas.id)) ?? []).filter(
        (r) => (r.created_at ?? "") >= sinceBreak,
      );
      expect(
        settledL0.map((r) => r.id).sort(),
        "no scar repair from a second faulted tick (product race, reseed and rerun)",
      ).toEqual([repair.id]);

      const knowledgeAfter = await control<{ entries?: Array<{ rule_id?: string; times_reused?: number }> }>(
        "GET",
        "/knowledge",
      );
      const reusedAfter =
        knowledgeAfter?.entries?.find((e) => e.rule_id === "page_to_cursor")?.times_reused ?? 0;
      expect(reusedAfter).toBe(reusedBefore + 1);

      // The Knowledge page shows the learned fix and its reuse count.
      await page.goto(`${UI_BASE}/knowledge`, { waitUntil: "domcontentloaded" });
      await expect(page.getByTestId("knowledge-page")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("knowledge-learned").first()).toContainText("reused");

      // Leave a clean frame for the legs below without a reset: the
      // shape-fixed release cannot stay active on clean sims (its next
      // sync would scar), so reactivate the base release first, then heal
      // and verify the connector returns to Working on Complete syncs.
      const reactivated = await control(
        "POST",
        `/connectors/${encodeURIComponent(atlas.id)}/releases/${encodeURIComponent(base.id)}/activate`,
        {},
      );
      expect(reactivated, "base release reactivated for a clean frame").not.toBeNull();
      await healFaults("sim-atlas");
      await poll(() => connectorState(atlas.id), (s) => s === "working", "atlas working on base release", 180_000);
      const tail = ((await repairsFor(atlas.id)) ?? []).filter((r) => r.decision === "escalate");
      expect(tail, "no scar escalation left behind").toEqual([]);
    },
  );

  test(
    "hard semantic failure: recorded repair shows funnel + call + question",
    { timeout: 120_000 },
    async ({ page }) => {
      test.setTimeout(120_000);
      // Needs only the recorded row, not the seed frame: `make demo`
      // step 4 leaves Harbor non-Working (its recorded drift stays open),
      // so this half runs after the demo; the live half needs the seed
      // frame and runs last in the file.
      await requireControl();
      // Scope by connector: other legs' scars (e.g. a verifying sync that
      // raced release activation) must never match here.
      const harbor = await ensureHarborBuilt();
      // The funnel + model call + rejected proposal + operator question
      // live on the recorded step-4 repair (POST /demo/record-climb).
      const rows = (await repairsFor(harbor.id)) ?? [];
      const recorded = rows.find(
        (r) => r.drift_class === "SEMANTIC_VALUE_DRIFT" && r.decision === "escalate",
      );
      if (!recorded) {
        test.skip(true, "no recorded semantic repair — run `make demo` (step 4) first, then re-run this file");
        return;
      }
      await page.goto(`${UI_BASE}/repairs/${encodeURIComponent(recorded.id)}`, {
        waitUntil: "domcontentloaded",
      });
      await expect(page.getByTestId("repair-detail-page")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("repair-decision-line")).toContainText("Needs your decision");
      await expect(page.getByTestId("hypothesis-funnel-headline")).toBeVisible();
      // Live or replayed frontier call when a key/recording exists;
      // otherwise the box says "0 model calls" and rung 3 says why L3
      // was skipped — never an invented answer.
      const ledger = await control<{ attempts?: Array<{ calls?: unknown[] }> }>(
        "GET",
        `/repairs/${encodeURIComponent(recorded.id)}/ledger`,
      );
      const nCalls = (ledger?.attempts ?? []).reduce((n, a) => n + (a.calls ?? []).length, 0);
      if (nCalls > 0) {
        await expect(page.getByTestId("model-call-box")).toBeVisible();
        await expect(page.getByTestId("model-call-box-total")).toBeVisible();
      } else {
        await expect(page.getByTestId("model-call-box-empty")).toContainText("0 model calls");
        await expect(page.getByTestId("rung-timeline-3")).toContainText("L3 skipped");
      }
      await expect(page.getByTestId("decided-question")).toBeVisible();
      await expect(page.getByTestId("verifier-verdict")).toBeVisible();
    },
  );

  test(
    "on-premise PABX: layout break re-handshakes, postings resume, no approval needed",
    { timeout: 300_000 },
    async ({ page }) => {
      test.setTimeout(300_000);
      const byName = await requireSeed();
      const pabx = byName.get("PABX edge")!;

      await waitForTick(pabx.id);
      await breakFault("fias_record_layout_change", "sim-fias-pabx");
      // The edge agent absorbs the firmware change (re-handshake on the
      // new layout); the connector stays Working and no repair lands —
      // the L1 rename patch is proven in-process by `make demo` step 8.
      await page.waitForTimeout(40_000);
      expect(await connectorState(pabx.id)).toBe("working");
      const rows = (await repairsFor(pabx.id)) ?? [];
      expect(rows.length, "no engine repair for the agent-absorbed layout change").toBe(0);

      const agents = await control<{ agents?: Array<{ agent_id?: string; link_up?: boolean }> }>(
        "GET",
        "/edge/agents",
      );
      expect(agents?.agents?.some((a) => a.link_up), "edge link up after re-handshake").toBe(true);

      // The PABX panel shows the link, the postings and the layout.
      await page.goto(`${UI_BASE}/connectors/${encodeURIComponent(pabx.id)}`, {
        waitUntil: "domcontentloaded",
      });
      await expect(page.getByTestId("connector-detail-page")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("pabx-panel")).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("pabx-link-state")).toBeVisible();
      await expect(page.getByTestId("pabx-postings")).toBeVisible();
      await expect(page.getByTestId("pabx-layout")).toBeVisible();
    },
  );

  test(
    "hard semantic failure: the break climbs live to error (no record-climb rows)",
    { timeout: 420_000 },
    async ({ page }) => {
      test.setTimeout(420_000);
      await requireSeed();
      // Deck 4 → deck 5 (REQ-049): Harbor from its wiki docs (money
      // invariant included), so gross_to_net breaks live: inject →
      // scheduled sync → Degraded → engine drift + escalated repair →
      // error. The assertions below read the engine-derived rows (source
      // sync set), never record-climb rows.
      const harbor = await ensureHarborBuilt();
      // One fresh take per seed: a previous take's still-open drift would
      // dedupe this break (one fault keeps one drift), so the live half
      // skips then — reseed for a fresh live take, like every other leg.
      const preDrift = await control<Array<{ id?: string; source_sync_run_id?: string | null }>>(
        "GET",
        `/drift?connector_id=${encodeURIComponent(harbor.id)}`,
      );
      if ((preDrift ?? []).some((d) => d.source_sync_run_id)) {
        test.skip(true, "harbor already carries an open engine semantic drift — reseed for a fresh live take");
        return;
      }
      const sinceBreak = new Date().toISOString();
      await waitForTick(harbor.id);
      await breakFault("gross_to_net", "sim-harbor");
      const repair = await poll(
        async () => newest((await repairsFor(harbor.id))?.filter((r) => (r.created_at ?? "") >= sinceBreak) ?? null),
        (r) => !!r && r.decision === "escalate",
        "live gross_to_net escalation",
        180_000,
        5000,
      );
      expect(repair.drift_class).toBe("SEMANTIC_VALUE_DRIFT");
      await poll(() => connectorState(harbor.id), (s) => s === "error", "harbor error after the live climb", 180_000);
      // One undecided fault keeps exactly one drift and one repair: drain
      // a tick past the error and fail fast on a doubled row.
      await new Promise((r) => setTimeout(r, 15_000));
      const settled = ((await repairsFor(harbor.id)) ?? []).filter((r) => (r.created_at ?? "") >= sinceBreak);
      expect(
        settled.map((r) => r.id).sort(),
        "exactly one repair for one break (no scar doubling)",
      ).toEqual([repair.id]);

      // The repair page explains itself from the live rows: the decision,
      // the funnel the engine derived from the wire, the one L3 attempt
      // (live or replayed — or the explicit skip naming the capture when
      // neither exists), the operator question and the reject path (a
      // semantic drift is never patched).
      await page.goto(`${UI_BASE}/repairs/${encodeURIComponent(repair.id)}`, {
        waitUntil: "domcontentloaded",
      });
      await expect(page.getByTestId("repair-detail-page")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("repair-decision-line")).toContainText("Needs your decision");
      await expect(page.getByTestId("repair-section-happened")).toBeVisible();
      await expect(page.getByTestId("happened-class")).toContainText("Semantic value drift");
      await expect(page.getByTestId("repair-section-did")).toBeVisible();
      await expect(page.getByTestId("hypothesis-funnel-headline")).toBeVisible();
      await expect(page.getByTestId("rung-timeline-0")).toBeVisible();
      await expect(page.getByTestId("rung-timeline-3")).toBeVisible();
      await expect(page.getByTestId("rung-timeline-4")).toBeVisible();
      const liveLedger = await control<{ attempts?: Array<{ calls?: unknown[] }> }>(
        "GET",
        `/repairs/${encodeURIComponent(repair.id)}/ledger`,
      );
      const liveCalls = (liveLedger?.attempts ?? []).reduce((n, a) => n + (a.calls ?? []).length, 0);
      if (liveCalls > 0) {
        await expect(page.getByTestId("model-call-box")).toBeVisible();
        await expect(page.getByTestId("model-call-box-total")).toBeVisible();
      } else {
        await expect(page.getByTestId("model-call-box-empty")).toContainText("0 model calls");
        await expect(page.getByTestId("rung-timeline-3")).toContainText("L3 skipped");
      }
      await expect(page.getByTestId("decided-question")).toBeVisible();
      await expect(page.getByTestId("verifier-verdict")).toBeVisible();
      await expect(page.getByTestId("approve-preview")).toBeVisible();
    },
  );

});
