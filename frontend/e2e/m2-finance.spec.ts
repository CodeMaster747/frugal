import { expect, test, type Page } from "@playwright/test";

/**
 * M2 smoke test — the financial core journey.
 *
 * The exit criterion this proves in a browser: import a CSV, see the
 * transactions, edit one, delete one. Plus the cold-start path, which is the
 * screen a new user actually lands on.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

const CSV = `Txn Date,Narration,Withdrawal,Deposit
01/08/2026,SALARY ACME TECH,,85000.00
03/08/2026,UPI/SWIGGY/883012,482.00,
04/08/2026,POS RELIANCE FRESH 4471,1250.00,
05/08/2026,AMAZON PAY 99213,2340.50,
`;

async function signUp(page: Page) {
  const email = `m2-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: /Welcome to Frugal/ })).toBeVisible({
    timeout: 20_000,
  });
  return email;
}

async function uploadCsv(page: Page) {
  await page.setInputFiles("#csv", {
    name: "statement.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(CSV),
  });
}

test.describe("M2 financial core", () => {
  test("a new user lands on the empty state, not a screen of zeroes", async ({ page }) => {
    await signUp(page);

    // Every engine is meaningless on an empty database, so the first screen's
    // only job is to get data in.
    await expect(page.getByRole("heading", { name: /Welcome to Frugal/ })).toBeVisible();
    await expect(page.getByTestId("load-demo")).toBeVisible();
    await expect(page.getByRole("link", { name: /Choose file/ })).toBeVisible();
  });

  test("demo data populates the product in one click", async ({ page }) => {
    await signUp(page);
    await page.getByTestId("load-demo").click();

    // A year of history, so health, forecasting and the advisor all have
    // something real to reason about (FR-2.10). M3 replaced the account list
    // on this screen with the dashboard, so assert the headline numbers.
    await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });
    await expect(page.getByText("Savings rate")).toBeVisible();

    await page.goto("/transactions");
    await expect(page.getByTestId("txn-row").first()).toBeVisible({ timeout: 20_000 });
  });

  test("imports a CSV, then edits and deletes a transaction", async ({ page }) => {
    await signUp(page);

    await page.goto("/transactions/import");
    await uploadCsv(page);
    await page.getByTestId("analyze").click();

    await expect(page.getByText("4 rows found")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("commit").click();

    const result = page.getByTestId("import-result");
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result.getByText("4")).toBeVisible();

    await page.getByRole("button", { name: "View transactions" }).click();
    await expect(page.getByTestId("txn-list")).toBeVisible();

    const rows = page.getByTestId("txn-row");
    await expect(rows).toHaveCount(4);

    // Edit: recategorise the first row.
    await rows.first().getByRole("combobox").selectOption({ label: "Groceries" });
    await expect(rows.first().getByRole("combobox")).toHaveValue(/.+/);

    // Delete: the row disappears.
    await rows
      .first()
      .getByRole("button", { name: /^Delete/ })
      .click();
    await expect(page.getByTestId("txn-row")).toHaveCount(3);
  });

  test("re-importing the same file creates no duplicates", async ({ page }) => {
    // The headline M2 exit criterion, seen from the user's side: the second
    // import reports everything as already present rather than doubling the
    // ledger.
    await signUp(page);

    for (const pass of [1, 2]) {
      await page.goto("/transactions/import");
      await uploadCsv(page);
      await page.getByTestId("analyze").click();
      await expect(page.getByText("4 rows found")).toBeVisible({ timeout: 20_000 });

      if (pass === 2) {
        await expect(page.getByText("4 already imported")).toBeVisible();
      }
      await page.getByTestId("commit").click();
      await expect(page.getByTestId("import-result")).toBeVisible({ timeout: 20_000 });
    }

    await page.goto("/transactions");
    await expect(page.getByTestId("txn-row")).toHaveCount(4);
  });

  test("adds a transaction by hand", async ({ page }) => {
    await signUp(page);

    // Import once so an account exists to attach the transaction to.
    await page.goto("/transactions/import");
    await uploadCsv(page);
    await page.getByTestId("analyze").click();
    await expect(page.getByText("4 rows found")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("commit").click();
    await expect(page.getByTestId("import-result")).toBeVisible({ timeout: 20_000 });

    await page.goto("/transactions");
    await page.getByTestId("toggle-add").click();
    await page.getByLabel("Amount").fill("640.00");
    // exact: "Merchant" would also match the "Search merchants" filter.
    await page.getByLabel("Merchant", { exact: true }).fill("Blue Tokai Coffee");
    await page.getByRole("button", { name: "Save transaction" }).click();

    // exact: the row also renders a screen-reader label containing the name.
    await expect(page.getByText("Blue Tokai Coffee", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("txn-row")).toHaveCount(5);
  });

  test("renders no unexpected console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error" && !/Failed to load resource.*401/i.test(m.text())) {
        errors.push(m.text());
      }
    });

    await signUp(page);
    await page.getByTestId("load-demo").click();
    await expect(page.getByTestId("kpi-row")).toBeVisible({ timeout: 40_000 });

    expect(errors).toEqual([]);
  });
});
