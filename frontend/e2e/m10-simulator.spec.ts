import { expect, test, type Page } from "@playwright/test";

/**
 * M10 smoke test — the decision simulator and the notification engine.
 *
 * The scenario arithmetic and the notification rules are settled by backend
 * tests. What only a browser can settle is the M10 exit criterion that *a
 * scenario produces a full before/after with an `Explanation`* — rendered by the
 * same panel as health, insights, forecasting, and the advisor, with no
 * engine-specific code.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUpWithData(page: Page) {
  const email = `m10-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
}

async function runScenario(page: Page, name: string) {
  await page.goto("/simulator");
  await page.getByRole("button", { name, exact: true }).click();
  await page.getByTestId("run-scenario").click();
  await expect(page.getByTestId("scenario-result")).toBeVisible({ timeout: 25_000 });
}

test.describe("M10 simulator & notifications", () => {
  test("a scenario produces a full before and after", async ({ page }) => {
    await signUpWithData(page);
    await runScenario(page, "Lose income");

    const card = page.getByTestId("scenario-result");
    await expect(card.getByTestId("outlook")).toHaveText(/Comfortable|Tight|Runs out/);
    // Both sides of every measure, so the reader is not doing arithmetic.
    await expect(card).toContainText("Savings now");
    await expect(card).toContainText("Monthly surplus");
    await expect(card).toContainText("Cover at the worst point");
    await expect(card).toContainText("→");
  });

  test("the explanation panel renders scenario output unchanged", async ({ page }) => {
    await signUpWithData(page);
    await runScenario(page, "Lose income");

    // Fifth engine through the same component, no new code.
    const panel = page.getByTestId("explanation-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("factor").first()).toBeVisible();
    await expect(panel).toContainText("Lowest point");
    await expect(panel).toContainText("Where you end up");
    // A scenario is not scored, so no factor claims to contribute points.
    await expect(panel).not.toContainText("pts · weight");
  });

  test("the projection is charted and offered as a table", async ({ page }) => {
    await signUpWithData(page);
    await runScenario(page, "Take a holiday");

    await expect(page.locator(".recharts-line-curve").first()).toBeVisible();
    await page.getByRole("button", { name: "View data" }).click();
    const table = page.getByRole("table").first();
    await expect(table).toContainText("Savings");
    await expect(table).toContainText("Monthly surplus");
  });

  test("changing template resets its inputs", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/simulator");

    await page.getByRole("button", { name: "Lose income", exact: true }).click();
    await expect(page.getByLabel("Months without income")).toHaveValue("6");

    await page.getByRole("button", { name: "Buy a vehicle", exact: true }).click();
    // A leftover value from the previous scenario must not carry over.
    await expect(page.getByLabel("Down payment")).toHaveValue("60000");
  });

  test("scenarios can be compared side by side", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/simulator");

    await page.getByTestId("compare-scenarios").click();

    const table = page.getByTestId("comparison");
    await expect(table).toBeVisible({ timeout: 25_000 });
    await expect(table.getByRole("row")).toHaveCount(4); // header + three
    // "Safest", not "best" — which is best depends on what the user wants.
    await expect(table).toContainText("leaves the most room");
  });

  test("notifications respect preferences", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/alerts");

    const prefs = page.getByTestId("preferences");
    await expect(prefs).toBeVisible({ timeout: 20_000 });

    // `click` then assert, rather than `uncheck`: the input is controlled by
    // server state, and `uncheck` requires the DOM to flip within the click
    // itself, which a React re-render does not guarantee under load.
    const toggle = page.getByLabel(/Goal milestones/);
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(toggle).not.toBeChecked();

    await page.getByTestId("generate-alerts").click();

    // A switched-off category is never recorded, not merely hidden.
    await expect(prefs).toContainText("never recorded at all");
    await page.reload();
    await expect(page.getByLabel(/Goal milestones/)).not.toBeChecked();
  });

  test("an empty alert feed reads as normal, not broken", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/alerts");

    const feed = page.getByTestId("notification-feed");
    if ((await feed.count()) === 0) {
      await expect(page.getByText(/That is the normal state/)).toBeVisible();
    }
  });

  test("renders no console errors", async ({ page }) => {
    const EXPECTED = /Failed to load resource.*(401|503)/i;
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !EXPECTED.test(m.text())) errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(e.message));

    await signUpWithData(page);
    await runScenario(page, "Change jobs");
    await page.goto("/alerts");
    await expect(page.getByTestId("preferences")).toBeVisible({ timeout: 20_000 });

    expect(errors).toEqual([]);
  });
});
