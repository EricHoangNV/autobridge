import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for AutoBridge W7.8 — UI journey + network isolation.
 *
 * Base URL is the Next.js UI (compose: http://localhost:8107, dev: http://localhost:3000).
 * The spec files live in tests/e2e/test_06_ui_journey.spec.ts (plan §4 W7),
 * tests/e2e/test_s6_richer_ui.spec.ts (plan §5 S6) and
 * tests/e2e/test_027_knowledge.spec.ts (REQ-027 post-demo knowledge state;
 * live-stack only, skips without the stack).
 *
 * Usage:
 *   make up                          # bring stack up (or npm run dev in ui/)
 *   npx playwright test              # runs test_06 journey + test_s6 richer UI
 *   E2E_UI_BASE=http://localhost:3000 npx playwright test
 *
 * In CI without Docker the specs fall back to mocked API routes so they stay
 * green (the static isolation is also asserted in tests/unit/test_autobridge_ui_isolation.py).
 */
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: ["test_06_ui_journey.spec.ts", "test_s6_richer_ui.spec.ts", "test_027_knowledge.spec.ts"],
  timeout: 30_000,
  expect: { timeout: 5000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_UI_BASE || process.env.UI_BASE || "http://localhost:8107",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Do not auto-start webServer here — the stack is managed by `make up`.
  // For local dev without compose, run `npm run dev` in ui/ first.
});
