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
  // The durable fix is to run these against a production build. That is now
  // what CI does (see `webServer` below); these settings stay because they
  // still absorb the cold start locally, where `npm run dev` is the point.
  // Raising the pool was still correct and is kept — see `db_pool_size` in the
  // backend config.
  //
  // CI is serial for a different reason: contention, not compilation. A GitHub
  // runner has two cores, and by the time the suite starts they are shared by
  // Postgres, Redis, MinIO, uvicorn, a Celery worker fitting Prophet models at
  // ~450 MB, the Next server, and a Chromium per test worker. Two test workers
  // on two cores left every remaining failure as a timeout — 38 of them on one
  // 40s assertion, with no functional error anywhere in the run. Serial is
  // slower and finishes.
  workers: process.env.CI ? 1 : 3,
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
    // A production build in CI; the dev server locally, where fast reload is
    // the whole point of running these by hand.
    //
    // The first CI run of this suite lost 9 tests and needed retries on 17
    // more, all of them timing out after a `load-demo` click while waiting for
    // a route the dev server was still compiling. The numbers above were tuned
    // on Apple silicon; a two-core runner compiling `/dashboard`, `/health`,
    // and `/forecast` on demand under two parallel workers is a different
    // machine entirely. `next start` serves routes that are already built, so
    // no request pays for being the first to arrive.
    command: process.env.CI ? "npm run build && npm run start" : "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    // In CI this budget has to cover a full production build before the server
    // answers at all, not just a process start.
    timeout: process.env.CI ? 300_000 : 120_000,
  },
});
