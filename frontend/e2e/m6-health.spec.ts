import { expect, test, type Page } from "@playwright/test";

/**
 * M6 smoke test — the health score, the insight feed, and the Explanation panel.
 *
 * The rubric arithmetic and the detector thresholds are settled by backend
 * tests. What only a browser can settle is the M6 exit criterion that
 * `<ExplanationPanel>` renders health output with **zero engine-specific
 * code** — the same component, unchanged, rendering both the health score and
 * an insight.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUpWithData(page: Page) {
  const email = `m6-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
}

test.describe("M6 health & insights", () => {
  test("shows a score with its full decomposition", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");

    await expect(page.getByTestId("score-value")).toBeVisible({ timeout: 20_000 });

    // Six factors, each named, valued, and explained — not a bare number.
    const factors = page.getByTestId("score-card").getByTestId("factor");
    await expect(factors).toHaveCount(6);
    await expect(factors.first()).toContainText("Savings rate");
  });

  test("the published rubric is reachable from the score", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");
    await expect(page.getByTestId("score-value")).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "How is this calculated?" }).click();

    const rubric = page.getByTestId("rubric");
    await expect(rubric).toBeVisible();
    // The weights the user is being scored against, in full.
    await expect(rubric).toContainText("Weights total 1.00");
    await expect(rubric.getByRole("row")).toHaveCount(7); // header + six metrics
  });

  test("the insight feed lists findings, ranked", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");

    await page.getByTestId("refresh-insights").click();
    await expect(page.getByTestId("insight-feed")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("insight-card").first()).toBeVisible();
  });

  test("every insight can explain itself", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");
    await page.getByTestId("refresh-insights").click();
    await expect(page.getByTestId("insight-card").first()).toBeVisible({ timeout: 20_000 });

    const card = page.getByTestId("insight-card").first();
    await card.getByRole("button", { name: /Why am I seeing this/ }).click();

    // The same panel component as the score card, with real factors in it.
    const panel = card.getByTestId("explanation-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("factor").first()).toBeVisible();
    await expect(panel).toContainText("confidence");
  });

  test("the explanation panel is engine-agnostic", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");
    await page.getByTestId("refresh-insights").click();
    await expect(page.getByTestId("insight-card").first()).toBeVisible({ timeout: 20_000 });

    await page
      .getByTestId("insight-card")
      .first()
      .getByRole("button", { name: /Why am I seeing this/ })
      .click();

    // The M6 exit criterion: one component, two engines (rubric_v1 and rule_v1),
    // rendering the same structure with no engine-specific branch.
    const panels = page.getByTestId("explanation-panel");
    expect(await panels.count()).toBeGreaterThanOrEqual(2);
    for (let i = 0; i < 2; i += 1) {
      await expect(panels.nth(i)).toContainText("Based on");
      await expect(panels.nth(i)).toContainText("confidence");
    }
  });

  test("dismissing an insight removes it from the feed", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");
    await page.getByTestId("refresh-insights").click();

    const cards = page.getByTestId("insight-card");
    await expect(cards.first()).toBeVisible({ timeout: 20_000 });
    const before = await cards.count();

    await cards.first().getByRole("button", { name: /^Dismiss:/ }).click();

    await expect(cards).toHaveCount(before - 1, { timeout: 15_000 });
  });

  test("a brand-new user is told why there is no score, not shown a zero", async ({ page }) => {
    const email = `m6-empty-${Date.now()}@example.com`;
    await page.goto("/register");
    await page.getByLabel("Name").fill("Arjun");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    // Wait for the post-registration landing rather than navigating straight
    // off: without it the /health request races the session being established.
    await expect(page.getByTestId("load-demo")).toBeVisible({ timeout: 20_000 });

    await page.goto("/health");

    await expect(page.getByTestId("score-value")).toContainText("Not enough history", {
      timeout: 20_000,
    });
    // A fabricated number would be worse than no number, so there must be none.
    await expect(page.getByTestId("score-value")).not.toContainText("/100");
    // And the reason has to be stated, not left to inference.
    await expect(page.getByTestId("caveats")).toBeVisible();
  });

  test("severity is never carried by colour alone", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/health");
    await page.getByTestId("refresh-insights").click();

    const card = page.getByTestId("insight-card").first();
    await expect(card).toBeVisible({ timeout: 20_000 });

    // The heading carries a severity word for screen readers and greyscale.
    const heading = card.getByRole("heading");
    const text = await heading.innerText();
    expect(text).toMatch(/For information|Worth attention|Needs action/);
  });

  test("renders no console errors", async ({ page }) => {
    const EXPECTED_401 = /Failed to load resource.*401/i;
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !EXPECTED_401.test(m.text())) errors.push(m.text());
    });

    await signUpWithData(page);
    await page.goto("/health");
    await page.getByTestId("refresh-insights").click();
    await expect(page.getByTestId("insight-card").first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "How is this calculated?" }).click();
    await expect(page.getByTestId("rubric")).toBeVisible();

    expect(errors).toEqual([]);
  });
});
