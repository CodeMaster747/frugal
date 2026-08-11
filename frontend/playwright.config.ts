import { defineConfig, devices } from "@playwright/test";

/**
 * One smoke spec per milestone, covering that milestone's user journey.
 *
 * The backend must be running (`make up`); the web server here only starts the
 * frontend. Specs that need live data assert against the demo seeder from M2.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",

  // Three workers and a 60s budget, because of the *dev server*, not the API.
  //
  // Chased as an API problem first: the demo seed was suspected, then the
  // connection pool. Both were measured and neither was it — a demo seed takes
  // 0.1s alone and 0.2s for three concurrent, and the API answers an advisor
  // evaluation in ~30ms. The real cause is Next's on-demand compilation: with
  // several workers hitting `/health`, `/forecast`, and `/advisor` cold at
  // once, the *first* request to each route takes seconds while the rest queue
  // behind it. Once warm, the whole suite runs in ~20s.
  //
  // The durable fix is to run these against a production build; until then
  // these settings absorb the cold start. Raising the pool was still correct
  // and is kept — see `db_pool_size` in the backend config.
  workers: process.env.CI ? 2 : 3,
  timeout: 60_000,

  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  // Clears the rate limiter, which a full run would otherwise exhaust after a
  // few passes — see the file.
  globalSetup: "./e2e/global-setup.ts",

  // Deletes the accounts this run created. Without it the local database grows
  // by ~24,000 transactions per run until signup outruns the test timeout — see
  // the file for the full account.
  globalTeardown: "./e2e/global-teardown.ts",

  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
