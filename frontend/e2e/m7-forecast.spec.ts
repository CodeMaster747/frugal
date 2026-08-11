import { expect, test, type Page } from "@playwright/test";

/**
 * M7 smoke test — the forecast, its confidence band, and its honesty.
 *
 * The tiers, the detector, and the backtest are settled by backend tests. What
 * only a browser can settle is the M7 exit criterion about the band: that it is
 * rendered as **one hue at low opacity, not a second series**. Three lines on a
 * chart implies three predictions; there is one prediction and a range.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUpWithData(page: Page) {
  const email = `m7-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
}

test.describe("M7 forecast", () => {
  test("shows a projection with its method and confidence", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");

    const card = page.getByTestId("forecast-card");
    await expect(card).toBeVisible({ timeout: 30_000 });

    // The response names the model; a chart cannot.
    await expect(card).toContainText(/Known commitments only|Recent averages|Full trend/);
    await expect(card).toContainText(/% confidence/);
    await expect(card).toContainText(/days of history/);
  });

  test("the band is one hue at low opacity, not a second series", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");
    await expect(page.getByTestId("forecast-card")).toBeVisible({ timeout: 30_000 });

    // Exactly one stroked line: the median. p10 and p90 are a fill.
    const lines = page.locator(".recharts-line-curve");
    await expect(lines).toHaveCount(1);

    // And the band is a translucent area, not an opaque one.
    const band = page.locator(".recharts-area-area").last();
    const opacity = await band.evaluate((el) => el.getAttribute("fill-opacity"));
    expect(Number(opacity)).toBeGreaterThan(0);
    expect(Number(opacity)).toBeLessThan(0.3);
  });

  test("every chart offers its numbers as a table", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");
    await expect(page.getByTestId("forecast-card")).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: "View data" }).click();
    const table = page.getByRole("table").first();
    await expect(table).toBeVisible();
    // Low / expected / high, so the band is readable without seeing colour.
    await expect(table).toContainText("Expected");
    await expect(table).toContainText("Low");
    await expect(table).toContainText("High");
  });

  test("the explanation panel renders forecast output unchanged", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");
    await expect(page.getByTestId("forecast-card")).toBeVisible({ timeout: 30_000 });

    // The same component as health and insights, third engine, no new code.
    const panel = page.getByTestId("explanation-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("Based on");
    await expect(panel.getByTestId("factor").first()).toBeVisible();
    // A forecast has no score, so no factor claims to contribute points to one.
    await expect(panel).not.toContainText("pts · weight");
  });

  test("changing the horizon changes the projection", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");
    await expect(page.getByTestId("forecast-card")).toBeVisible({ timeout: 30_000 });

    await expect(page.getByTestId("forecast-card")).toContainText("Projected in 90 days");
    await page.getByRole("button", { name: "30d", exact: true }).click();
    await expect(page.getByTestId("forecast-card")).toContainText("Projected in 30 days");
  });

  test("a scenario is clearly hypothetical and does not stick", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");
    await expect(page.getByTestId("forecast-card")).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("Amount", { exact: true }).fill("200000");
    await page.getByTestId("run-scenario").click();

    // Labelled as a what-if, not presented as the user's forecast.
    await expect(page.getByText(/Showing a hypothetical/)).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "Clear" }).click();
    await expect(page.getByText(/Showing a hypothetical/)).toHaveCount(0);
  });

  test("detected commitments are listed with their cadence", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/forecast");

    const table = page.getByTestId("recurring-table");
    await expect(table).toBeVisible({ timeout: 30_000 });
    // The merchant is stored normalised and lowercased; `capitalize` is a CSS
    // display concern, so the DOM text is lowercase.
    await expect(table).toContainText(/salary/i);
    await expect(table).toContainText(/rent/i);
    await expect(table).toContainText("monthly");
  });

  test("a new user is told why there is no forecast, not shown a flat line", async ({ page }) => {
    const email = `m7-empty-${Date.now()}@example.com`;
    await page.goto("/register");
    await page.getByLabel("Name").fill("Arjun");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByTestId("load-demo")).toBeVisible({ timeout: 20_000 });

    await page.goto("/forecast");

    const declined = page.getByTestId("forecast-declined");
    await expect(declined).toBeVisible({ timeout: 20_000 });
    await expect(declined).toContainText("14 days");
    // No fabricated series behind the refusal.
    await expect(page.getByTestId("forecast-card")).toHaveCount(0);
  });

  test("renders no console errors", async ({ page }) => {
    const EXPECTED = /Failed to load resource.*(401|503)/i;
    const errors: string[] = [];
    page.on("console", (m) => {
      // The 503 is the documented "not enough history" answer, not a fault.
      if (m.type() === "error" && !EXPECTED.test(m.text())) errors.push(m.text());
    });

    await signUpWithData(page);
    await page.goto("/forecast");
    await expect(page.getByTestId("forecast-card")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "60d", exact: true }).click();
    await expect(page.getByTestId("forecast-card")).toContainText("Projected in 60 days");

    expect(errors).toEqual([]);
  });
});
