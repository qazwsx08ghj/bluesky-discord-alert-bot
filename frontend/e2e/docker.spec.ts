// Checks that need no fake Bluesky or Discord, so they also run against a real install:
// config/tests_docker.py runs them against its Docker test stack (E2E_BASE_URL) with
//   npx playwright test --project=panel --grep @docker
// Nothing here contacts Bluesky or Discord: no Check now, no tests, no lookups, no resumed accounts.
import { expect, test } from "@playwright/test";
import { expectNoSideways, watch } from "./helpers";

test.describe("@docker the built panel", () => {
  test("every page loads with its security policy and without errors", async ({ page }) => {
    const w = watch(page);
    for (const [path, heading] of [
      ["/accounts", "Accounts"],
      ["/activity", "Activity"],
      ["/settings", "Settings"],
    ]) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      const headers = response!.headers();
      expect(headers["content-security-policy"]).toContain("script-src 'self'");
      expect(headers["x-frame-options"]).toBe("DENY");
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    }
    await expect(page.getByRole("button", { name: /^Bot status: / })).toBeVisible();
    const cookies = await page.context().cookies();
    expect(cookies.find((c) => c.name === "csrftoken")?.sameSite).toBe("Strict");
    await w.expectClean();
  });

  test("changes made in the panel are saved (CSRF works end to end)", async ({ page, request }) => {
    const w = watch(page);
    await page.goto("/settings");
    const select = page.getByLabel("Check every");
    const before = await select.inputValue();
    const other = before === "10" ? "15" : "10";
    await select.selectOption(other);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText(`Saved. The bot now checks every ${other} minutes.`)).toBeVisible();
    expect((await (await request.get("/api/settings/")).json()).poll_interval_minutes).toBe(Number(other));
    await select.selectOption(before);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText(new RegExp(`Saved\\. The bot now checks every ${before} minute`))).toBeVisible();

    await page.getByRole("button", { name: "Add channel" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a Discord channel" });
    await dialog.getByLabel("Channel name").fill("e2e-check");
    await dialog.getByLabel("Webhook URL").fill("https://discord.com/api/webhooks/999/e2e-check-token");
    await dialog.getByRole("button", { name: "Add channel" }).click();
    await expect(page.getByText("Added #e2e-check.", { exact: false })).toBeVisible();
    await expect(page.getByText("e2e-check-token")).toHaveCount(0);
    await page.getByRole("button", { name: "More actions for #e2e-check" }).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    const confirm = page.getByRole("alertdialog");
    if (await confirm.getByRole("heading", { name: "#e2e-check is the default channel" }).isVisible()) {
      // On an install with no other channel, the first one becomes the default and can't be deleted.
      await confirm.getByRole("button", { name: "OK" }).click();
    } else {
      await confirm.getByRole("button", { name: "Delete channel" }).click();
      await expect(page.getByText("Deleted #e2e-check.")).toBeVisible();
    }
    await w.expectClean();
  });

  test("light, dark and tablet-sized windows", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/accounts");
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(14, 17, 22)");
    await page.emulateMedia({ colorScheme: "light" });
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(246, 247, 249)");
    for (const size of [{ width: 1024, height: 768 }, { width: 768, height: 1024 }]) {
      await page.setViewportSize(size);
      for (const path of ["/accounts", "/settings"]) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expectNoSideways(page);
      }
    }
  });
});
