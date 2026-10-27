import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for AutoBridge W7.8 — UI journey + network isolation.
 *
 * Base URL is the Next.js UI (compose: http://localhost:8107, dev: http://localhost:3000).
 * The spec file lives in tests/e2e/test_06_ui_journey.spec.ts per plan §4 W7.
 *
 * Usage:
 *   make up                          # bring stack up (or npm run dev in ui/)
 *   npx playwright test              # runs test_06 journey
 *   E2E_UI_BASE=http://localhost:3000 npx playwright test
 *
 * In CI without Docker the spec falls back to mocked API routes so it is still
 * green (the static isolation is also asserted in tests/unit/test_autobridge_ui_isolation.py).
 */
export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "test_06_ui_journey.spec.ts",
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
