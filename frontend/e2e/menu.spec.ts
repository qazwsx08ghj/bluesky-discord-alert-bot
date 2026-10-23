// The "more actions" menus float above the page (components/ui.tsx, Menu): never cut off by the
// accounts table's scroll area, no extra scrollbars, placed inside the window, following their
// button, and every action and key still works.
import { expect, test, type Locator, type Page } from "@playwright/test";
import { reset, watch } from "./helpers";

test.beforeEach(async ({ request }) => reset(request, "working"));

const rows = (page: Page) => page.locator("table.acc-table tbody tr");
const triggerIn = (row: Locator) => row.getByRole("button", { name: /^More actions for / });
const openMenu = (page: Page) => page.getByRole("menu");

/** Measure after the short opening animation (a 4 px slide), so positions are final. */
async function settled(page: Page) {
  const menu = openMenu(page);
  await expect(menu).toBeVisible();
  await menu.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
  return menu;
}

async function box(locator: Locator) {
  const b = await locator.boundingBox();
  expect(b, "visible on the page").not.toBeNull();
  return b!;
}

/** Sizes of the table's scroll area and of the page: a menu must not make either scroll. */
async function scrollSizes(page: Page) {
  return page.evaluate(() => {
    const area = document.querySelector(".scroll")!;
    const doc = document.documentElement;
    return [area.scrollWidth, area.scrollHeight, area.clientWidth, area.clientHeight, doc.scrollWidth, doc.scrollHeight];
  });
}

/** The whole menu can be seen: inside the window, and the menu itself is what's at its corners and centre. */
async function expectFullyVisible(page: Page) {
  const menu = await settled(page);
  const b = await box(menu);
  const view = page.viewportSize()!;
  expect(b.x).toBeGreaterThanOrEqual(7.5);
  expect(b.y).toBeGreaterThanOrEqual(7.5);
  expect(b.x + b.width).toBeLessThanOrEqual(view.width - 7.5);
  expect(b.y + b.height).toBeLessThanOrEqual(view.height - 7.5);
  const inset = 12; // past the 10 px rounded corners
  const points = [
    [b.x + inset, b.y + inset], [b.x + b.width - inset, b.y + inset], [b.x + inset, b.y + b.height - inset],
    [b.x + b.width - inset, b.y + b.height - inset], [b.x + b.width / 2, b.y + b.height / 2],
  ];
  const onTop = await page.evaluate((pts) => pts.map(([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('[role="menu"]'))), points);
  expect(onTop, "nothing covers or cuts off any corner").toEqual([true, true, true, true, true]);
  expect(await page.locator('.scroll [role="menu"]').count(), "not inside the table's scroll area").toBe(0);
}

/** The Accounts page has finished loading: nothing shows a loading placeholder or is marked busy,
 * and Recent activity shows its entries (or says there are none). Measure page sizes only after
 * this: the activity list arrives on its own and makes the page taller, sooner or later. */
async function pageLoaded(page: Page) {
  const recent = page.getByRole("region", { name: "Recent activity" });
  await expect(recent.locator(".feed > li").first().or(recent.getByText("Nothing yet")).or(
    recent.getByText("Couldn't load the activity"))).toBeVisible();
  await expect(page.locator(".skel")).toHaveCount(0);
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
}

/** A tiny step for scroll and resize handlers (they wait for the next frame). */
const settle = (page: Page) => page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));

test("the last account's menu floats above the table, whole, without scrollbars", async ({ page }) => {
  const w = watch(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/accounts");
  const last = rows(page).last();
  await expect(last).toBeVisible();
  await pageLoaded(page);
  const before = await scrollSizes(page);
  await triggerIn(last).click();
  await expectFullyVisible(page);
  expect(await scrollSizes(page), "opening the menu adds no scrollbar").toEqual(before);
  const menu = await box(openMenu(page));
  const button = await box(triggerIn(last));
  expect(Math.abs(menu.x + menu.width - (button.x + button.width)), "right edges line up").toBeLessThanOrEqual(1);
  await w.expectClean();
});

test("the same holds when the activity list loads late (as on a slow computer)", async ({ page }) => {
  // The activity answer is held back until the table is on screen, so the page grows only after
  // the table has appeared: the order seen on GitHub's slower machines.
  let release!: () => void;
  const tableShown = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/activity/**", async (route) => {
    await tableShown;
    await route.continue();
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/accounts");
  const last = rows(page).last();
  await expect(last).toBeVisible();
  await expect(page.getByRole("region", { name: "Recent activity" }).locator(".skel").first()).toBeVisible();
  release();
  await pageLoaded(page);
  const before = await scrollSizes(page);
  await triggerIn(last).click();
  await expectFullyVisible(page);
  expect(await scrollSizes(page), "opening the menu adds no scrollbar").toEqual(before);
});

test("near the bottom of the window it opens above its button", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto("/accounts");
  const trigger = triggerIn(rows(page).last());
  // Scroll so the button sits just above the window's bottom edge.
  await trigger.evaluate((el) => window.scrollBy({ top: el.getBoundingClientRect().bottom - (window.innerHeight - 24), behavior: "instant" }));
  await settle(page);
  const button = await box(trigger);
  expect(button.y + button.height).toBeGreaterThan(700 - 60);
  await trigger.click();
  await expectFullyVisible(page);
  const menu = await box(openMenu(page));
  expect(menu.y + menu.height).toBeLessThanOrEqual(button.y);
  await expect(openMenu(page)).toHaveAttribute("data-side", "above");
});

test("near the right edge of a phone-sized window it stays inside", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await page.goto("/accounts");
  await pageLoaded(page);
  for (const index of [0, -1]) {
    const row = index === 0 ? rows(page).first() : rows(page).last();
    const trigger = triggerIn(row);
    await trigger.scrollIntoViewIfNeeded();
    const before = await scrollSizes(page);
    await trigger.click();
    await expectFullyVisible(page);
    expect(await scrollSizes(page)).toEqual(before);
    await page.keyboard.press("Escape");
    await expect(openMenu(page)).toHaveCount(0);
  }
});

test("it follows its button when the page scrolls or the window changes size", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await page.goto("/accounts");
  const trigger = triggerIn(rows(page).nth(1));
  await trigger.evaluate((el) => window.scrollBy({ top: el.getBoundingClientRect().top - 200, behavior: "instant" }));
  await settle(page);
  await trigger.click();
  await settled(page);
  // Menu and button measured in the same instant.
  const gap = () => page.evaluate(() => {
    const menu = document.querySelector('[role="menu"]')!.getBoundingClientRect();
    const button = document.querySelector('[aria-expanded="true"]')!.getBoundingClientRect();
    return menu.top - button.bottom;
  });
  const first = await gap();
  expect(first).toBeGreaterThanOrEqual(0);

  const scrolledTo = await page.evaluate(() => {
    window.scrollTo({ top: window.scrollY + 80, behavior: "instant" });
    return window.scrollY;
  });
  expect(scrolledTo, "the page really scrolled").toBeGreaterThan(0);
  await settle(page);
  await expect(openMenu(page)).toBeVisible();
  expect(Math.abs((await gap()) - first), "still right below its button after scrolling").toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 1000, height: 600 });
  await settle(page);
  await expect(openMenu(page)).toBeVisible();
  const menu = await box(openMenu(page));
  const button = await box(trigger);
  expect(Math.abs(menu.x + menu.width - (button.x + button.width)), "lined up again after resizing").toBeLessThanOrEqual(1);
  await expectFullyVisible(page);
});

test("it follows its button when the table itself scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/accounts");
  await pageLoaded(page);
  // A wider table than the page (as with long names), so its scroll area scrolls sideways.
  // (Set on the element: the panel's security policy refuses added style tags.)
  await page.locator("table.acc-table").evaluate((el: HTMLElement) => (el.style.minWidth = "1800px"));
  const area = page.locator(".scroll").first();
  expect(await area.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  const trigger = triggerIn(rows(page).first());
  await area.evaluate((el) => (el.scrollLeft = el.scrollWidth));  // bring the buttons into view
  await settle(page);
  const before = await scrollSizes(page);
  await trigger.click();
  await expectFullyVisible(page);
  expect(await scrollSizes(page), "no scrollbar added to the table or the page").toEqual(before);
  await area.evaluate((el) => (el.scrollLeft -= 12));
  await settle(page);
  await expect(openMenu(page)).toBeVisible();
  const placed = await page.evaluate(() => {
    const menu = document.querySelector('[role="menu"]')!.getBoundingClientRect();
    const button = document.querySelector('[aria-expanded="true"]')!.getBoundingClientRect();
    return { menu: menu.right, expected: Math.min(button.right, document.documentElement.clientWidth - 8) };
  });
  expect(Math.abs(placed.menu - placed.expected), "moved with its button (kept inside the window)").toBeLessThanOrEqual(1);

  // Scrolled until the button is hidden by the table's own scroll area: the menu closes too.
  await area.evaluate((el) => (el.scrollLeft = 0));
  await settle(page);
  await expect(openMenu(page)).toHaveCount(0);
});

test("it closes when its button scrolls out of sight", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await page.goto("/accounts");
  await triggerIn(rows(page).first()).click();
  await expect(openMenu(page)).toBeVisible();
  await page.evaluate(() => window.scrollBy({ top: 2000, behavior: "instant" }));
  await settle(page);
  const stillSeen = await triggerIn(rows(page).first()).evaluate((el) => el.getBoundingClientRect().bottom > 0);
  if (!stillSeen) await expect(openMenu(page)).toHaveCount(0);
});

test("a click outside or Escape closes it, and Escape gives focus back", async ({ page }) => {
  await page.goto("/accounts");
  const trigger = triggerIn(rows(page).first());
  await trigger.click();
  await expect(openMenu(page)).toBeVisible();
  await page.getByRole("heading", { level: 1, name: "Accounts" }).click();
  await expect(openMenu(page)).toHaveCount(0);
  await expect(trigger).toHaveAttribute("aria-expanded", "false");

  await trigger.click();
  await expect(openMenu(page).getByRole("menuitem", { name: "Edit" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(openMenu(page)).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("works with the keyboard alone", async ({ page }) => {
  await page.goto("/accounts");
  const trigger = triggerIn(rows(page).first());
  await trigger.focus();
  await page.keyboard.press("ArrowDown");
  const menu = openMenu(page);
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(trigger).toHaveAttribute("aria-controls", (await menu.getAttribute("id"))!);
  await expect(menu.getByRole("menuitem", { name: "Edit" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Open on Bluesky" })).toBeFocused();
  await page.keyboard.press("End");
  await expect(menu.getByRole("menuitem", { name: "Remove" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Edit" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(menu).toHaveCount(0);
  expect(await page.evaluate(() => document.activeElement !== document.body), "focus goes on along the page").toBe(true);

  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(openMenu(page).getByRole("menuitem", { name: "Edit" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /^Edit / })).toBeVisible();
});

test("every account action still works", async ({ page, context }) => {
  await context.route("https://bsky.app/**", (route) => route.fulfill({ status: 200, body: "profile" }));
  await page.goto("/accounts");
  const row = rows(page).first();
  const name = (await row.locator(".who b").textContent())!;

  await triggerIn(row).click();
  await openMenu(page).getByRole("menuitem", { name: "Edit" }).click();
  await expect(page.getByRole("dialog", { name: `Edit ${name}` })).toBeVisible();
  await page.getByRole("dialog", { name: `Edit ${name}` }).getByRole("button", { name: "Cancel" }).click();

  await triggerIn(row).click();
  const popup = page.waitForEvent("popup");
  await openMenu(page).getByRole("menuitem", { name: "Open on Bluesky" }).click();
  expect((await popup).url()).toMatch(/^https:\/\/bsky\.app\/profile\//);

  await triggerIn(row).click();
  await openMenu(page).getByRole("menuitem", { name: "Remove" }).click();
  await expect(page.getByRole("alertdialog", { name: `Stop watching ${name}?` })).toBeVisible();
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await expect(rows(page).filter({ hasText: name })).toHaveCount(1);
});

test("the channel menus in Settings float the same way", async ({ page }) => {
  await page.goto("/settings");
  const trigger = page.getByRole("button", { name: "More actions for #art-drops" });
  await trigger.click();
  await expectFullyVisible(page);
  await expect(openMenu(page).getByRole("menuitem", { name: "Delete" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});
