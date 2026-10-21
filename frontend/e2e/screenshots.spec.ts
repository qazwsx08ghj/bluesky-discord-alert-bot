// Screenshots of the control panel for the documentation (docs/screenshots/).
//   npm run screenshots
// They use the demo server's made-up accounts and channels (config/testing/demo_server.py),
// never real data.
import { expect, test, type Page } from "@playwright/test";
import { reset } from "./helpers";

const OUT = "../docs/screenshots/";

async function shot(page: Page, name: string, fullPage = false) {
  await page.mouse.move(0, 0);
  await page.waitForTimeout(400); // let entrance animations settle
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage, animations: "disabled" });
}

test.describe.configure({ mode: "serial" });

test("first visit: setup", async ({ page, request }) => {
  await reset(request, "empty");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Set up your bot" })).toBeVisible();
  await shot(page, "01-setup");
});

test("accounts, light and dark", async ({ page, request }) => {
  await reset(request, "working");
  await page.goto("/accounts");
  await expect(page.getByRole("row", { name: /Mira Draws/ })).toBeVisible();
  await shot(page, "02-accounts");
  await page.emulateMedia({ colorScheme: "dark" });
  await shot(page, "03-accounts-dark");
});

test("adding an account", async ({ page }) => {
  await page.goto("/accounts");
  await page.getByRole("button", { name: "Add account" }).click();
  const drawer = page.getByRole("dialog", { name: "Add an account to watch" });
  await drawer.getByLabel("Bluesky handle").fill("studio-kite.bsky.social");
  await expect(drawer.getByText("Studio Kite", { exact: true })).toBeVisible();
  await shot(page, "04-add-account");
});

test("check all in the background", async ({ page }) => {
  await page.goto("/accounts");
  await page.getByRole("button", { name: "Check all now" }).click();
  await expect(page.getByText("Check all finished")).toBeVisible({ timeout: 20_000 });
  await page.getByText(/^Each account/).click();
  await shot(page, "05-check-all");
});

test("activity", async ({ page }) => {
  await page.goto("/activity");
  await expect(page.getByText(/Commissions open for March/).first()).toBeVisible();
  await shot(page, "06-activity");
});

test("settings", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByText(/Connected as/)).toBeVisible();
  await shot(page, "07-settings", true);
});

test("bot status", async ({ page }) => {
  await page.goto("/accounts");
  await page.getByRole("button", { name: /Bot status:/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await shot(page, "08-status");
});

test("tablet sizes", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/accounts");
  await expect(page.getByRole("row", { name: /Mira Draws/ })).toBeVisible();
  await shot(page, "09-tablet-landscape");
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/accounts");
  await expect(page.getByRole("row", { name: /Mira Draws/ })).toBeVisible();
  await shot(page, "10-tablet-portrait-dark");
});

test("a problem, and its fix", async ({ page, request }) => {
  await reset(request, "problem");
  await page.goto("/accounts");
  await expect(page.getByRole("button", { name: "Update the Bluesky login" })).toBeVisible();
  await shot(page, "11-problem");
});

test("when the bot isn't running", async ({ page, request }) => {
  await reset(request, "working");
  await page.goto("/accounts");
  await expect(page.getByRole("row", { name: /Mira Draws/ })).toBeVisible();
  await page.route("**/api/**", (route) => route.abort("connectionrefused"));
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("Can't reach the bot", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  await shot(page, "12-offline");
});
