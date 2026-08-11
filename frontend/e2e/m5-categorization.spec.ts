import { expect, test, type Page } from "@playwright/test";

/**
 * M5 smoke test — auto-categorisation and the review queue.
 *
 * The backend tests already prove the categoriser is correct. What only a
 * browser can settle is whether a *suggestion* is distinguishable from a fact:
 * that the user can see which categories a machine chose, accept one in a
 * click, and have a correction stick.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUpWithData(page: Page) {
  const email = `m5-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await page.getByTestId("load-demo").click();
  await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
}

test.describe("M5 categorisation", () => {
  test("a new transaction is categorised from its merchant alone", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/transactions");

    await page.getByTestId("toggle-add").click();
    await page.getByLabel("Amount", { exact: true }).fill("480");
    await page.getByLabel("Merchant", { exact: true }).fill("SWIGGY*ORDER 7781");
    // Wait on the write itself rather than on the list catching up. Asserting
    // against a timeout instead made this flake under parallel workers, and a
    // test that depends on how loaded the machine is tells you nothing.
    const created = page.waitForResponse(
      (r) => r.url().includes("/api/v1/transactions") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Save transaction" }).click();
    expect((await created).status()).toBe(201);

    // Narrow by the raw narration, which is unique: the demo data has its own
    // Swiggy rows, and same-day transactions have no guaranteed order.
    const row = page
      .getByTestId("txn-row")
      .filter({ hasText: "SWIGGY*ORDER 7781" })
      .first();
    await expect(row).toBeVisible({ timeout: 15_000 });

    // The user chose no category; the categoriser did. A non-empty select value
    // is the whole claim of M5 on the write path.
    await expect(row.getByRole("combobox")).not.toHaveValue("");
    // And it landed on the right one, from a mangled narration.
    await expect(row.getByRole("combobox")).toHaveValue(
      await row.locator("option", { hasText: "Food Delivery" }).getAttribute("value") ?? "",
    );
  });

  test("the review queue holds only machine suggestions", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/transactions");

    await page.getByTestId("toggle-review").click();
    await expect(page.getByText(/categorised automatically/i)).toBeVisible();

    const rows = page.getByTestId("txn-row");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });

    // Every row in the queue offers a one-click confirm; a row nobody needs to
    // review would not.
    const count = await rows.count();
    for (let i = 0; i < Math.min(count, 5); i += 1) {
      await expect(rows.nth(i).getByRole("button", { name: /^Confirm / })).toBeVisible();
    }
  });

  test("confirming a suggestion removes it from the queue", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/transactions");
    await page.getByTestId("toggle-review").click();

    const rows = page.getByTestId("txn-row");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    const before = await rows.count();

    await rows.first().getByRole("button", { name: /^Confirm / }).click();

    // Confirming is the whole interaction: the row is settled and leaves.
    await expect(rows).toHaveCount(before - 1, { timeout: 15_000 });
  });

  test("a correction sticks and leaves the queue", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/transactions");
    await page.getByTestId("toggle-review").click();

    const rows = page.getByTestId("txn-row");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    const before = await rows.count();

    const select = rows.first().getByRole("combobox");
    const options = select.locator("option");
    // Any option other than the current one and the empty first entry.
    const current = await select.inputValue();
    const alternative = await options
      .filter({ hasNotText: "Uncategorised" })
      .nth(1)
      .getAttribute("value");
    expect(alternative).toBeTruthy();
    expect(alternative).not.toBe(current);

    await select.selectOption(alternative!);

    // A human decision is final: the row is reviewed and drops out.
    await expect(rows).toHaveCount(before - 1, { timeout: 15_000 });
  });

  test("a suggestion is visibly provisional, and not by colour alone", async ({ page }) => {
    await signUpWithData(page);
    await page.goto("/transactions");
    await page.getByTestId("toggle-review").click();

    const row = page.getByTestId("txn-row").first();
    await expect(row).toBeVisible({ timeout: 15_000 });

    // The accessible label carries the reason, so the state survives a
    // colourblind reader, a screen reader, and a greyscale print.
    const label = await row.getByRole("combobox").getAttribute("aria-label");
    const described = label ?? (await row.locator("label.sr-only").innerText());
    expect(described.toLowerCase()).toContain("suggested");
  });

  test("renders no console errors", async ({ page }) => {
    // The pre-auth probe on first paint 401s by design -- the app asks whether
    // a session exists before it knows. Same exclusion as the M0 spec.
    const EXPECTED_401 = /Failed to load resource.*401/i;
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !EXPECTED_401.test(m.text())) errors.push(m.text());
    });

    await signUpWithData(page);
    await page.goto("/transactions");
    await page.getByTestId("toggle-review").click();
    await expect(page.getByTestId("txn-row").first()).toBeVisible({ timeout: 15_000 });

    expect(errors).toEqual([]);
  });
});
