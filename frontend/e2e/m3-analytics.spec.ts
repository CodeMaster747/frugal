import { expect, test, type Page } from "@playwright/test";

/**
 * M3 smoke test — the dashboard and the chart system.
 *
 * Three of these are design gates that only a browser can settle: that no
 * dual-axis chart exists, that every chart exposes a keyboard-reachable table,
 * and that the palette actually swaps between themes rather than being
 * inverted.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUpWithData(page: Page) {
  const email = `m3-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
}

test.describe("M3 analytics", () => {
  test("shows the headline numbers", async ({ page }) => {
    await signUpWithData(page);

    // "Net worth" is also a chart title, so scope to the KPI row.
    await expect(page.getByTestId("kpi-row").getByText("Net worth")).toBeVisible();
    await expect(page.getByText("Income this month")).toBeVisible();
    await expect(page.getByText("Spent this month")).toBeVisible();
    await expect(page.getByText("Savings rate")).toBeVisible();
  });

  test("renders the charts", async ({ page }) => {
    await signUpWithData(page);

    await expect(page.getByRole("heading", { name: "Income vs expense" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Net worth", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Where it went" })).toBeVisible();
  });

  test("every chart exposes a keyboard-reachable data table", async ({ page }) => {
    // The accessibility obligation the ChartContainer enforces: a chart is not
    // finished until a non-sighted user can read the same numbers.
    await signUpWithData(page);

    const figures = page.locator("figure");
    // A retrying assertion, not a snapshot count: charts mount as their data
    // arrives, so counting immediately races the last one.
    await expect(figures).toHaveCount(3);

    for (let i = 0; i < 3; i++) {
      // Scoped per figure: the toggle's label flips to "Hide data" once
      // opened, so a name-based locator set would shrink under the loop.
      const toggle = figures.nth(i).getByRole("button", { name: /View data|Hide data/ });
      await toggle.focus();
      await expect(toggle).toBeFocused(); // reachable without a mouse
      await page.keyboard.press("Enter");
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await expect(figures.nth(i).getByRole("table")).toBeVisible();
    }
  });

  test("every chart carries a screen-reader summary", async ({ page }) => {
    await signUpWithData(page);

    await expect(page.locator("figure")).toHaveCount(3);
    const summaries = await page
      .locator("figure .sr-only")
      .evaluateAll((nodes) => nodes.map((n) => n.textContent?.trim() ?? ""));

    expect(summaries.length).toBeGreaterThanOrEqual(3);
    // A summary must say something, not just exist.
    for (const s of summaries) expect(s.length).toBeGreaterThan(20);
  });

  test("no chart uses a dual axis", async ({ page }) => {
    // The design review gate. Two y-scales let the author put the crossing
    // point anywhere, which is why it is the most misread chart there is.
    await signUpWithData(page);
    await expect(page.locator("figure")).toHaveCount(3);

    const axisCounts = await page
      .locator("figure")
      .evaluateAll((figures) =>
        figures.map((f) => f.querySelectorAll(".recharts-yAxis").length),
      );

    for (const count of axisCounts) expect(count).toBeLessThanOrEqual(1);
  });

  test("the chart palette swaps between themes rather than inverting", async ({ page }) => {
    await signUpWithData(page);

    const seriesOne = () =>
      page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim(),
      );

    await page.getByRole("radio", { name: "Light" }).click();
    expect(await seriesOne()).toBe("#2a78d6");

    await page.getByRole("radio", { name: "Dark" }).click();
    // A different step of the same hue, validated against the dark surface.
    expect(await seriesOne()).toBe("#3987e5");
    await expect(page.getByTestId("kpi-row")).toBeVisible();
  });

  test("the dashboard reflects a new transaction immediately", async ({ page }) => {
    // Version-based cache invalidation, from the user's side: a TTL would leave
    // a window where the dashboard disagrees with the ledger.
    await signUpWithData(page);

    const spent = page
      .getByTestId("kpi-row")
      .locator("div")
      .filter({ hasText: "Spent this month" });
    const before = await spent.textContent();

    await page.goto("/transactions");
    await page.getByTestId("toggle-add").click();
    await page.getByLabel("Amount").fill("9999.00");
    await page.getByLabel("Merchant", { exact: true }).fill("Cache Probe");
    await page.getByRole("button", { name: "Save transaction" }).click();
    await expect(page.getByText("Cache Probe", { exact: true })).toBeVisible({
      timeout: 20_000,
    });

    await page.goto("/dashboard");
    await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 20_000 });
    await expect(spent).not.toHaveText(before ?? "");
  });

  test("compares like-for-like periods, not a partial month against a full one", async ({
    page,
  }) => {
    // Comparing five days of August against all of July would show a ~50% drop
    // in spending every month, which is arithmetically true and completely
    // misleading. The label has to say what is actually being compared.
    await signUpWithData(page);

    await expect(page.getByText(/vs same period last month/).first()).toBeVisible();
  });

  test("renders no unexpected console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error" && !/Failed to load resource.*401/i.test(m.text())) {
        errors.push(m.text());
      }
    });

    await signUpWithData(page);
    // The ChartContainer logs an error if a chart declares a low-contrast slot
    // without direct labels or a table, so this also guards that obligation.
    expect(errors).toEqual([]);
  });
});
