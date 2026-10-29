/**
 * REQ-027 knowledge-page check — the state `make demo` must leave behind.
 *
 * After a full keyed `make demo` (8/8 ok), `GET /knowledge` lists atlaspms
 * under vocabulary v1, meridianpms under v2 (with v1 refused) and the PABX
 * connector under v3, plus at least one learned entry whose `times_reused`
 * went up on the step-3 inject-again leg. The UI `/knowledge` page renders
 * the same state.
 *
 * Runs against the live stack only: without a reachable control plane the
 * spec skips (the unit/component suites cover the shape offline).
 */

import { test, expect } from "@playwright/test";

const UI_BASE = process.env.E2E_UI_BASE || process.env.UI_BASE || "http://localhost:8107";
const CONTROL_BASE = process.env.E2E_CONTROL_BASE || "http://localhost:8101";

type KnowledgeConnector = {
  connector_id: string;
  name: string;
  vocabulary: string;
  refused_vocabularies?: string[];
};

type KnowledgeEntry = {
  vendor: string;
  rule_id: string;
  times_reused: number;
};

type Knowledge = {
  entries: KnowledgeEntry[];
  connectors: KnowledgeConnector[];
  counts: Record<string, number>;
};

async function fetchKnowledge(): Promise<Knowledge | null> {
  try {
    const res = await fetch(`${CONTROL_BASE}/knowledge`);
    if (!res.ok) return null;
    return (await res.json()) as Knowledge;
  } catch {
    return null;
  }
}

test.describe("REQ-027 post-demo knowledge state", () => {
  test("knowledge API lists v1/v2/v3 profiles and a reused learned entry", async () => {
    const knowledge = await fetchKnowledge();
    if (!knowledge) test.skip(true, "control plane unreachable — run after `make local-up` + `make demo`");
    const k = knowledge as Knowledge;
    expect(k.entries.length).toBeGreaterThanOrEqual(1);
    const reused = k.entries.filter((e) => e.times_reused >= 1);
    expect(reused.length).toBeGreaterThanOrEqual(1);
    expect(reused.some((e) => e.rule_id === "page_to_cursor")).toBe(true);

    const byName = new Map(k.connectors.map((c) => [c.name, c]));
    const atlas = byName.get("atlaspms");
    expect(atlas?.vocabulary).toBe("v1");
    const meridian = byName.get("meridianpms");
    expect(meridian?.vocabulary).toBe("v2");
    expect(meridian?.refused_vocabularies ?? []).toContain("v1");
    const pabx = byName.get("PABX edge");
    expect(pabx?.vocabulary).toBe("v3");
  });

  test("knowledge UI page shows the profiles and the reused entry", async ({ page }) => {
    const knowledge = await fetchKnowledge();
    if (!knowledge) test.skip(true, "control plane unreachable — run after `make local-up` + `make demo`");
    await page.goto(`${UI_BASE}/knowledge`);
    await expect(page.getByTestId("knowledge-page")).toBeVisible();
    await expect(page.getByTestId("knowledge-vocabulary-v1")).toContainText("atlaspms");
    await expect(page.getByTestId("knowledge-vocabulary-v2")).toContainText("meridianpms");
    await expect(page.getByTestId("knowledge-vocabulary-v3")).toContainText("PABX edge");
    await expect(page.getByTestId("knowledge-learned").first()).toContainText("reused");
  });
});
