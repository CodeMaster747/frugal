import { expect, test } from "@playwright/test";

/**
 * M0 smoke test — the foundation, verified in a real browser.
 *
 * These assertions moved to /login in M1, when `/` became a protected route.
 * `/` is now the public home screen and the dashboard lives at `/dashboard`,
 * so the guard is exercised against that instead. The properties under test are
 * unchanged — themed shell, working theme toggle, no console errors — and they
 * are still checked on /login, which is where the theme toggle was first proven.
 *
 * Requires the backend stack (`make up`).
 */

test.describe("M0 foundation", () => {
  test("renders the public shell", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expect(page.getByRole("radiogroup", { name: "Colour theme" })).toBeVisible();
  });

  test("an anonymous visitor is sent to sign in", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("toggles between light and dark", async ({ page }) => {
    await page.goto("/login");
    const html = page.locator("html");

    await page.getByRole("radio", { name: "Dark" }).click();
    await expect(html).toHaveClass(/dark/);
    await expect(page.getByRole("radio", { name: "Dark" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    await page.getByRole("radio", { name: "Light" }).click();
    await expect(html).not.toHaveClass(/dark/);
  });

  test("dark mode is a selected palette, not an inverted one", async ({ page }) => {
    await page.goto("/login");
    const seriesOne = () =>
      page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim(),
      );

    await page.getByRole("radio", { name: "Light" }).click();
    expect(await seriesOne()).toBe("#2a78d6");

    await page.getByRole("radio", { name: "Dark" }).click();
    // A different step of the same hue, validated against the dark surface --
    // not the light value flipped.
    expect(await seriesOne()).toBe("#3987e5");
  });

  test("the theme toggle is keyboard operable", async ({ page }) => {
    await page.goto("/login");

    for (let i = 0; i < 10; i++) {
      await page.keyboard.press("Tab");
      const role = await page.evaluate(() => document.activeElement?.getAttribute("role"));
      if (role === "radio") return; // reached it without a mouse (NFR-6)
    }
    throw new Error("Theme toggle was not reachable by keyboard within 10 tab stops");
  });

  test("renders no console errors", async ({ page }) => {
    // Worth keeping precise rather than deleting: this check is what caught a
    // real bug where a 401 response carried no CORS headers, which no backend
    // test could see.
    //
    // An anonymous visitor's session-restore call legitimately returns 401,
    // and Chrome logs every failed HTTP response to the console. That one line
    // is expected; anything else is not.
    const EXPECTED_401 = /Failed to load resource.*401/i;

    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !EXPECTED_401.test(m.text())) consoleErrors.push(m.text());
    });
    // Uncaught exceptions are never expected.
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto("/login", { waitUntil: "networkidle" });

    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
});
