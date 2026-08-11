import { expect, test, type Page } from "@playwright/test";

/**
 * M9 smoke test — the watchlist, price history, and reliability scores.
 *
 * The rubric and the drop detector are settled by backend tests. What only a
 * browser can settle is the M9 exit criterion that *reliability scores show
 * their factors* — and, just as important, that the interface says plainly what
 * the score is **not** claiming. FR-9.2 replaced a "scam risk" label for legal
 * reasons, and the disclaimer reaching the user is part of that decision, not
 * decoration.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUp(page: Page) {
  const email = `m9-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByTestId("load-demo")).toBeVisible({ timeout: 30_000 });
}

async function trackFirst(page: Page, query = "macbook air") {
  await page.goto("/watchlist");
  await page.getByLabel("Track something").fill(query);
  await page.getByTestId("search-track").click();
  await expect(page.getByTestId("track-results")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Track" }).first().click();
  await expect(page.getByTestId("wishlist-item").first()).toBeVisible({ timeout: 20_000 });
}

test.describe("M9 market intelligence", () => {
  test("tracking a product shows its price history immediately", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);

    await page.getByRole("button", { name: "Price history" }).first().click();

    // Backfilled, so the chart is useful now rather than in three months.
    const chart = page.locator(".recharts-line-curve");
    await expect(chart.first()).toBeVisible({ timeout: 20_000 });
    // "Lowest recorded" appears in the summary line, the chart's accessible
    // summary, and the table -- scoped rather than made unique, because all
    // three are correct.
    await expect(page.getByText(/Lowest recorded/).first()).toBeVisible();
  });

  test("the history is offered as a table, not only a chart", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);
    await page.getByRole("button", { name: "Price history" }).first().click();
    await expect(page.getByTestId("offers")).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "View data" }).click();
    const table = page.getByRole("table").first();
    await expect(table).toContainText("Best price");
    await expect(table).toContainText("Sellers");
  });

  test("every offer shows its reliability factors", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);
    await page.getByRole("button", { name: "Price history" }).first().click();

    const offers = page.getByTestId("offers");
    await expect(offers).toBeVisible({ timeout: 20_000 });

    // The band is a phrase, never a colour alone.
    await expect(offers).toContainText(/protected/);

    await offers.getByRole("button", { name: "Why?" }).first().click();
    await expect(offers).toContainText("Return window");
    await expect(offers).toContainText("Warranty");
    await expect(offers).toContainText("pts · weight");
  });

  test("the score states what it is not claiming", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);
    await page.getByRole("button", { name: "Price history" }).first().click();
    await expect(page.getByTestId("offers")).toBeVisible({ timeout: 20_000 });

    await page.getByTestId("offers").getByRole("button", { name: "Why?" }).first().click();

    // FR-9.2. The disclaimer is the reason this feature is allowed to exist in
    // this form, so it has to reach the user.
    await expect(page.getByText(/not the seller's character or trustworthiness/)).toBeVisible();
  });

  test("the reliability rubric is published in-product", async ({ page }) => {
    await signUp(page);
    await page.goto("/watchlist");

    await page.getByRole("button", { name: "How is reliability scored?" }).click();

    const rubric = page.getByTestId("reliability-rubric");
    await expect(rubric).toBeVisible({ timeout: 20_000 });
    await expect(rubric).toContainText("not a judgement about the seller");
    await expect(rubric).toContainText("Return window");
    await expect(rubric).toContainText("weight");
  });

  test("nothing in the interface accuses a seller", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);
    await page.getByRole("button", { name: "Price history" }).first().click();
    await expect(page.getByTestId("offers")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("offers").getByRole("button", { name: "Why?" }).first().click();
    await page.getByRole("button", { name: "How is reliability scored?" }).click();

    const text = (await page.locator("body").innerText()).toLowerCase();
    for (const word of ["scam", "fraud", "fake", "counterfeit", "untrustworthy", "suspicious"]) {
      // "trustworthiness" appears only inside the disclaimer denying the claim.
      expect(text.includes(word)).toBe(false);
    }
  });

  test("a tracked item can be removed", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);

    await page.getByRole("button", { name: /^Stop tracking/ }).first().click();
    await expect(page.getByText(/Nothing tracked yet/)).toBeVisible({ timeout: 20_000 });
  });

  test("checking for drops is a normal outcome when there are none", async ({ page }) => {
    await signUp(page);
    await trackFirst(page);

    await page.getByTestId("check-drops").click();

    // No alert is the common case and must not read as a failure. Asserted on
    // the error text rather than `role="alert"`: the dev overlay renders an
    // empty one of those, so the generic locator was testing the framework.
    await expect(page.getByTestId("wishlist-item").first()).toBeVisible();
    await expect(page.getByTestId("price-alerts")).toHaveCount(0);
    await expect(page.getByText(/already tracking|failed/i)).toHaveCount(0);
  });

  test("renders no console errors", async ({ page }) => {
    const EXPECTED = /Failed to load resource.*(401|503)/i;
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !EXPECTED.test(m.text())) errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(e.message));

    await signUp(page);
    await trackFirst(page);
    await page.getByRole("button", { name: "Price history" }).first().click();
    await expect(page.getByTestId("offers")).toBeVisible({ timeout: 20_000 });

    expect(errors).toEqual([]);
  });
});
