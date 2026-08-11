import { expect, test, type Page } from "@playwright/test";

import { FOOTER_COLUMNS } from "../src/features/marketing/content";

/**
 * The public landing surface.
 *
 * `/` used to be the dashboard, so an anonymous visitor's first screen was a
 * redirect to sign-in. The two properties worth guarding are that it no longer
 * redirects, and that the footer's links go somewhere real — a footer that
 * looks complete and is full of dead anchors is the failure mode this page
 * invites.
 *
 * Requires the backend stack (`make up`) for the signed-in case only.
 */

const PASSWORD = "CorrectHorse9Battery";

async function signUp(page: Page) {
  const email = `landing-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Priya");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL("/dashboard", { timeout: 20_000 });
}

test.describe("landing page", () => {
  test("an anonymous visitor lands on the home screen, not on sign-in", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      "Personal finance that tells you what to do next.",
    );
  });

  test("Get Started goes to registration", async ({ page }) => {
    await page.goto("/");

    // The hero CTA, not the header's — scoped to main so the two cannot be
    // confused if the header one is ever removed.
    await page.getByRole("main").getByRole("link", { name: "Get Started" }).first().click();

    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  });

  test("a signed-in visitor is offered the dashboard instead", async ({ page }) => {
    await signUp(page);
    await page.goto("/");

    // Still the home screen: no redirect, only a different destination.
    await expect(page).toHaveURL("/");
    const cta = page.getByRole("main").getByRole("link", { name: "Open dashboard" }).first();
    await expect(cta).toBeVisible();
    await expect(cta).toHaveAttribute("href", "/dashboard");
    await expect(page.getByRole("link", { name: "Get Started" })).toHaveCount(0);
  });

  test("the hero renders without JavaScript-gated reveals hiding it", async ({ page }) => {
    await page.goto("/");

    // Every revealed block must settle to full opacity. A stuck observer would
    // leave the page technically present and visually blank.
    const reveals = page.locator("[data-reveal]");
    await expect(reveals.first()).toBeVisible();

    for (const handle of await reveals.all()) {
      await handle.scrollIntoViewIfNeeded();
      await expect(handle).toHaveCSS("opacity", "1");
    }
  });

  test("every footer link resolves to a real page and a real anchor", async ({ page }) => {
    await page.goto("/");

    for (const column of FOOTER_COLUMNS) {
      await expect(
        page.getByRole("navigation", { name: column.heading }).getByRole("heading"),
      ).toHaveText(column.heading);

      for (const link of column.links) {
        if (link.href.startsWith("mailto:")) continue;

        const [path, anchor] = link.href.split("#");
        const response = await page.goto(path);
        expect(response?.status(), `${link.href} should not 404`).toBe(200);

        if (anchor) {
          await expect(
            page.locator(`#${anchor}`),
            `${link.href} should have a #${anchor} target`,
          ).toBeVisible();
        }
      }
    }
  });
});
