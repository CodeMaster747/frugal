import { expect, test, type Page } from "@playwright/test";

/**
 * M8 smoke test — the flagship, and the v1 acceptance journey.
 *
 * The rubric and the verdicts are settled by the scenario matrix; the response
 * shape by the integration tests. What only a browser can settle is the M8 exit
 * criterion journey end to end: *search → evaluate → read the explanation →
 * compare EMI → evaluate an alternative*.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUpWithData(page: Page) {
  const email = `m8-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
}

async function evaluateFirstResult(page: Page, query: string) {
  await page.goto("/advisor");
  await page.getByLabel("What are you thinking of buying?").fill(query);
  await page.getByTestId("search-products").click();
  await expect(page.getByTestId("search-results")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Should I?" }).first().click();
  await expect(page.getByTestId("advice-card")).toBeVisible({ timeout: 20_000 });
}

test.describe("M8 purchase advisor", () => {
  test("the v1 journey: search, evaluate, explain, compare, alternative", async ({ page }) => {
    await signUpWithData(page);
    await evaluateFirstResult(page, "macbook pro");

    // A verdict, in words and with an icon — never colour alone.
    const verdict = page.getByTestId("verdict");
    await expect(verdict).toBeVisible();
    await expect(verdict).toHaveText(/Buy it|Wait|Not recommended/);

    // The reasoning, rendered by the same panel as health, insights, forecast.
    const panel = page.getByTestId("explanation-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("factor")).toHaveCount(7);

    // The EMI comparison, with total interest beside every monthly figure.
    await expect(page.getByTestId("emi-table")).toBeVisible();
    await expect(page.getByTestId("emi-table")).toContainText("Total interest");

    // And an alternative can be evaluated in one click.
    const alternatives = page.getByTestId("alternatives");
    await expect(alternatives).toBeVisible();
    const firstAlternative = await alternatives.getByRole("listitem").first().innerText();
    await alternatives.getByRole("button", { name: "Ask about this" }).first().click();

    await expect(page.getByTestId("advice-card")).toContainText(
      firstAlternative.split("\n")[0].slice(0, 20),
      { timeout: 20_000 },
    );
  });

  test("every verdict shows its factor decomposition", async ({ page }) => {
    await signUpWithData(page);
    await evaluateFirstResult(page, "macbook pro");

    const panel = page.getByTestId("explanation-panel");
    // Weights sum to 1.00 and contributions sum to the score — asserted in the
    // backend; here we only check the user can see them at all.
    await expect(panel).toContainText("Emergency fund after purchase");
    await expect(panel).toContainText("pts · weight");
    await expect(panel).toContainText("confidence");
  });

  test("the impact on the user's position is shown before and after", async ({ page }) => {
    await signUpWithData(page);
    await evaluateFirstResult(page, "macbook pro");

    const dumbbell = page.getByTestId("impact-dumbbell");
    await expect(dumbbell).toBeVisible();
    await expect(dumbbell).toContainText("Liquid savings");
    await expect(dumbbell).toContainText("Emergency fund");
    // Both sides of each comparison, so the reader is not doing arithmetic.
    await expect(dumbbell).toContainText("→");
  });

  test("EMI plans beyond the user's means are marked, not hidden", async ({ page }) => {
    await signUpWithData(page);
    await evaluateFirstResult(page, "macbook pro");

    const table = page.getByTestId("emi-table");
    // Seeing that three months would take 91% of income is exactly why twelve
    // is the sensible choice — hiding it would remove the comparison.
    await expect(table).toContainText("beyond your means");
    await expect(table).toContainText("36 months");
  });

  test("a manual price gets advice even with no catalogue match", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/advisor");

    await page.getByLabel("What is it?").fill("Handmade oak desk");
    await page.getByLabel("Price", { exact: true }).fill("62000");
    await page.getByTestId("evaluate-manual").click();

    await expect(page.getByTestId("advice-card")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("explanation-panel")).toBeVisible();
  });

  test("an unmatched search offers manual entry rather than a dead end", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/advisor");

    await page.getByLabel("What are you thinking of buying?").fill("zqx9 nonexistent thing");
    await page.getByTestId("search-products").click();

    await expect(page.getByText(/Nothing in the catalogue matches/)).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("evaluate-manual")).toBeVisible();
  });

  test("a capped verdict explains which limit was crossed", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/advisor");

    // Large enough against this persona to trip a hard constraint.
    await page.getByLabel("What is it?").fill("A very expensive thing");
    await page.getByLabel("Price", { exact: true }).fill("480000");
    await page.getByTestId("evaluate-manual").click();

    await expect(page.getByTestId("advice-card")).toBeVisible({ timeout: 20_000 });
    const constraints = page.getByTestId("constraints");
    await expect(constraints).toBeVisible();
    // Not a bare refusal: the rule that fired is stated in words.
    await expect(constraints).toContainText(/cushion|negative|savings|ceiling|cash/i);
  });

  test("a wait verdict always carries a date", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/advisor");

    await page.getByLabel("What is it?").fill("Something borderline");
    await page.getByLabel("Price", { exact: true }).fill("400000");
    await page.getByTestId("evaluate-manual").click();

    const card = page.getByTestId("advice-card");
    await expect(card).toBeVisible({ timeout: 20_000 });

    if ((await card.getAttribute("data-verdict")) === "wait") {
      await expect(page.getByTestId("affordable-from")).toBeVisible();
      await expect(page.getByTestId("affordable-from")).toContainText("affordable from");
    }
  });

  test("renders no console errors", async ({ page }) => {
    const EXPECTED = /Failed to load resource.*(401|503)/i;
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !EXPECTED.test(m.text())) errors.push(m.text());
    });

    await signUpWithData(page);
    await evaluateFirstResult(page, "iphone");

    expect(errors).toEqual([]);
  });
});
