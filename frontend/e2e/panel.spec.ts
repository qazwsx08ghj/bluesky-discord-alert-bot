// End-to-end tests: the built React panel in Chromium, against the real Django app and checker
// (config/testing/demo_server.py: temporary database, fake Bluesky and Discord, no network).
import { expect, test } from "@playwright/test";
import { discordMessages, expectNoSideways, reset, watch } from "./helpers";

test.describe("first-time setup", () => {
  test("from an empty install to the first watched account", async ({ page, request }) => {
    await reset(request, "empty");
    const w = watch(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByRole("heading", { name: "Set up your bot" })).toBeVisible();

    await page.getByLabel("Your Bluesky handle").fill("alertbot-demo.bsky.social");
    await page.getByLabel("App Password", { exact: true }).fill("demo-demo-demo-demo");
    await page.getByRole("button", { name: "Test and continue" }).click();

    await page.getByLabel("Channel name").fill("announcements");
    await page.getByLabel("Webhook URL").fill("https://discord.com/api/webhooks/1189000000000000001/demo-token-announcements");
    await page.getByRole("button", { name: "Save and continue" }).click();

    await page.getByRole("button", { name: "Send test message" }).click();
    await expect(page.getByText("Test message sent. Check #announcements in Discord.")).toBeVisible();
    expect(await discordMessages(request)).toHaveLength(1);

    await page.getByRole("button", { name: "Add account", exact: true }).first().click();
    const drawer = page.getByRole("dialog", { name: "Add an account to watch" });
    await drawer.getByLabel("Bluesky handle").fill("studio-kite.bsky.social");
    await expect(drawer.getByText("Studio Kite", { exact: true })).toBeVisible();
    await drawer.getByRole("button", { name: "Add account" }).click();
    await expect(page.getByText(/First check done for Studio Kite\. Noted 1 existing post; none were sent/)).toBeVisible();
    await expect(page.getByText("Your bot is set up")).toBeVisible();
    // The first check sent nothing: only the test message reached Discord.
    expect(await discordMessages(request)).toHaveLength(1);

    await page.getByRole("link", { name: "Go to Accounts" }).click();
    await expect(page.getByRole("row", { name: /Studio Kite/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Bot status: Running/ })).toBeVisible();
    await w.expectClean();
  });
});

test.describe("every day", () => {
  test.beforeEach(async ({ request }) => reset(request, "working"));

  test("Check now announces a new post in Discord", async ({ page, request }) => {
    const w = watch(page);
    await page.goto("/accounts");
    await request.get("/__demo__/post/mira-draws.bsky.social/?text=New%20print%20shop%20is%20open");
    await page.getByRole("button", { name: "Check Mira Draws now" }).click();
    await expect(page.getByText(/Checked Mira Draws\./)).toBeVisible();
    await expect.poll(async () => (await discordMessages(request)).map((m) => m.text)).toContain("New print shop is open");
    await page.getByRole("link", { name: "View all" }).click();
    await expect(page.getByText("“New print shop is open”")).toBeVisible();
    await w.expectClean();
  });

  test("Check all runs in the background and survives a reload", async ({ page }) => {
    const w = watch(page);
    await page.goto("/accounts");
    await page.getByRole("button", { name: "Check all now" }).click();
    const panel = page.getByRole("region", { name: /Check all/ });
    await expect(panel).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: /Check all/ })).toBeVisible();
    await expect(page.getByText("Check all finished")).toBeVisible({ timeout: 20_000 });
    await page.getByText(/^Each account/).click(); // the per-account list folds away once the check is done
    await expect(page.getByRole("region", { name: /Check all/ }).getByText("Paused, so it wasn't checked.")).toBeVisible();
    await expect(page.getByRole("region", { name: /Check all/ }).getByText(/First check done\. Noted 1 existing post/)).toBeVisible();
    await w.expectClean();
  });

  test("pause, resume and remove an account", async ({ page }) => {
    await page.goto("/accounts");
    await page.getByRole("button", { name: "Pause Night Bus News" }).click();
    await expect(page.getByRole("row", { name: /Night Bus News/ }).getByText("Paused")).toBeVisible();
    await page.getByRole("button", { name: "Resume Night Bus News" }).click();
    await expect(page.getByRole("row", { name: /Night Bus News/ }).getByText("Active")).toBeVisible();
    await page.getByRole("button", { name: "More actions for Night Bus News" }).click();
    await page.getByRole("menuitem", { name: "Remove" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Remove account" }).click();
    await expect(page.getByText("Stopped watching Night Bus News.")).toBeVisible();
    await expect(page.getByRole("row", { name: /Night Bus News/ })).toHaveCount(0);
  });

  test("settings: channels, frequency and appearance", async ({ page, request }) => {
    const w = watch(page);
    await page.goto("/settings");
    await page.getByRole("button", { name: "Add channel" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a Discord channel" });
    await dialog.getByLabel("Channel name").fill("news");
    await dialog.getByLabel("Webhook URL").fill("https://discord.com/api/webhooks/5550001/demo-token-news");
    await dialog.getByRole("button", { name: "Add channel" }).click();
    await page.getByRole("button", { name: "Send a test message to #news" }).click();
    await expect(page.getByText("Test message sent. Check #news in Discord.")).toBeVisible();
    expect((await discordMessages(request)).at(-1)?.channel_token).toBe("demo-token-news");

    await page.getByLabel("Check every").selectOption("10");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Saved. The bot now checks every 10 minutes.")).toBeVisible();
    const settings = await (await request.get("/api/settings/")).json();
    expect(settings.poll_interval_minutes).toBe("10");

    await page.getByRole("button", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(background).toBe("rgb(14, 17, 22)");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await w.expectClean();
  });

  test("deleting a channel moves its accounts to the default", async ({ page, request }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "More actions for #art-drops" }).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete channel" }).click();
    await expect(page.getByText("Deleted #art-drops.")).toBeVisible();
    const accounts = (await (await request.get("/api/accounts/")).json()).results;
    expect(accounts.every((a: { channel_id: number | null }) => a.channel_id === null)).toBe(true);
  });
});

test.describe("problems", () => {
  test("a rejected login leads to its fix, and clears once fixed", async ({ page, request }) => {
    await reset(request, "problem");
    const w = watch(page);
    await page.goto("/accounts");
    await expect(page.getByRole("button", { name: /Bot status: Needs attention/ })).toBeVisible();
    await page.getByRole("button", { name: "Update the Bluesky login" }).click();
    await expect(page).toHaveURL(/\/settings#bluesky$/);
    await expect(page.getByText(/Bluesky didn't accept this login/)).toBeVisible();
    await page.getByLabel("App Password", { exact: true }).fill("good-good-good-good");
    await page.getByRole("button", { name: "Test login" }).click();
    await expect(page.getByText(/The login works/)).toBeVisible();
    await page.getByRole("button", { name: "Save login" }).click();
    await expect(page.getByText("Bluesky login saved. The next check uses it.")).toBeVisible();
    await page.goto("/accounts");
    await page.getByRole("button", { name: "Check Pixel Forge Games now" }).click();
    await expect(page.getByText(/Checked Pixel Forge Games\./)).toBeVisible();
    await expect(page.getByRole("button", { name: /Bot status: Running/ })).toBeVisible({ timeout: 15_000 });
    await w.expectClean();
  });
});

test.describe("keyboard, layout and addresses", () => {
  test.beforeEach(async ({ request }) => reset(request, "working"));

  test("keyboard only: skip link, drawer focus, Escape returns focus", async ({ page }) => {
    await page.goto("/accounts");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    const add = page.getByRole("button", { name: "Add account" });
    await add.focus();
    await page.keyboard.press("Enter");
    const drawer = page.getByRole("dialog", { name: "Add an account to watch" });
    await expect(drawer.getByLabel("Bluesky handle")).toBeFocused();
    for (let i = 0; i < 15; i += 1) {
      await page.keyboard.press("Tab");
      // Inside the drawer, or (after its last control) the browser's own toolbar: never the page behind it.
      const where = await drawer.evaluate((d) => (d.contains(document.activeElement) ? "drawer" : document.activeElement === document.body ? "browser" : "page"));
      expect(where, "focus never reaches the page behind the drawer").not.toBe("page");
    }
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(add).toBeFocused();
  });

  for (const size of [
    { width: 1024, height: 768 },
    { width: 768, height: 1024 },
  ]) {
    test(`tablet ${size.width}×${size.height}: no sideways scrolling, actions in view`, async ({ page }) => {
      await page.setViewportSize(size);
      for (const path of ["/accounts", "/activity", "/settings"]) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expectNoSideways(page);
      }
      await page.goto("/accounts");
      const actions = page.getByRole("button", { name: "More actions for Mira Draws" });
      await actions.scrollIntoViewIfNeeded();
      await expect(actions).toBeInViewport();
      const box = (await actions.boundingBox())!;
      expect(box.x + box.width, "the row's last button isn't cut off").toBeLessThanOrEqual(size.width);
      await page.getByRole("button", { name: "Check Mira Draws now" }).click();
      await expect(page.getByText(/Checked Mira Draws\./)).toBeVisible();
    });
  }

  test("every panel address loads directly, and unknown ones say so", async ({ page }) => {
    for (const [path, heading] of [
      ["/accounts", "Accounts"],
      ["/activity", "Activity"],
      ["/settings", "Settings"],
      ["/settings/", "Settings"],
    ]) {
      const response = await page.goto(path);
      expect(response?.headers()["content-security-policy"]).toContain("default-src 'self'");
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    }
    const missing = await page.goto("/no-such-page");
    expect(missing?.status()).toBe(404);
  });
});
