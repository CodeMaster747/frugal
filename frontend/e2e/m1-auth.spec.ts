import { expect, test, type Page } from "@playwright/test";

/**
 * M1 smoke test — the authentication journey, in a real browser.
 *
 * Two of these can only be proven here: that the access token never reaches
 * web storage, and that the refresh cookie is unreadable by JavaScript.
 * Neither is observable from a backend test.
 *
 * Requires the backend stack (`make up`).
 */

const PASSWORD = "CorrectHorse9Battery";

/** Unique per run: registration is rate-limited per IP and emails are unique. */
function newEmail() {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

async function signUp(page: Page, email = newEmail()) {
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  // Assert the authenticated shell rather than a specific heading: the landing
  // page content changes every milestone, but "signed in" does not.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible({ timeout: 20_000 });
  return email;
}

test.describe("M1 authentication", () => {
  test("registers and lands signed in", async ({ page }) => {
    await signUp(page);
    await expect(page).toHaveURL("/dashboard");
  });

  test("the access token never touches web storage", async ({ page }) => {
    // The M1 exit criterion. A token in localStorage is readable by any
    // successful XSS payload; in memory it dies with the tab.
    await signUp(page);

    const storage = await page.evaluate(() => ({
      local: Object.entries(localStorage).map(([k, v]) => `${k}=${v}`),
      session: Object.entries(sessionStorage).map(([k, v]) => `${k}=${v}`),
    }));

    const all = [...storage.local, ...storage.session].join("\n");
    expect(all).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}/); // a JWT
    expect(all.toLowerCase()).not.toContain("access_token");
  });

  test("the refresh cookie is httpOnly and unreadable by scripts", async ({
    page,
    context,
  }) => {
    await signUp(page);

    const cookie = (await context.cookies()).find((c) => c.name === "frugal_refresh");
    expect(cookie, "refresh cookie should be set").toBeTruthy();
    expect(cookie!.httpOnly).toBe(true);
    expect(cookie!.path).toBe("/api/v1/auth");

    // document.cookie must not expose it.
    expect(await page.evaluate(() => document.cookie)).not.toContain("frugal_refresh");
  });

  test("signs out and back in", async ({ page }) => {
    const email = await signUp(page);

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("the session survives a page reload", async ({ page }) => {
    // Proves silent restore: the in-memory token is gone after reload, so the
    // app must exchange the httpOnly cookie for a new one.
    await signUp(page);
    await page.reload();

    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible({
      timeout: 20_000,
    });
  });

  test("redirects an anonymous visitor away from a protected page", async ({ page }) => {
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/login/);
  });

  test("rejects wrong credentials without revealing whether the account exists", async ({
    page,
  }) => {
    const email = await signUp(page);
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("WrongPassword123");
    await page.getByRole("button", { name: "Sign in" }).click();

    // Scoped to the form: Next's route announcer is also role="alert".
    await expect(page.locator("form").getByText(/Incorrect email or password/i)).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("shows validation errors before submitting", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Name").fill("Priya");
    await page.getByLabel("Email").fill("not-an-email");
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText("Enter a valid email")).toBeVisible();
    await expect(page.getByText("At least 12 characters")).toBeVisible();
  });

  test("deletes the account and blocks sign-in afterwards", async ({ page }) => {
    const email = await signUp(page);

    await page.goto("/settings");
    await page.getByLabel('Type "DELETE" to confirm').fill("DELETE");
    await page.getByRole("button", { name: "Delete my account" }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.locator("form").getByText(/Incorrect email or password/i)).toBeVisible();
  });

  test("the auth pages are keyboard operable", async ({ page }) => {
    await page.goto("/login");

    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      const label = await page.evaluate(
        () => (document.activeElement as HTMLInputElement | null)?.type ?? null,
      );
      if (label === "email") return; // reached the first field without a mouse
    }
    throw new Error("Email field was not reachable by keyboard");
  });
});
