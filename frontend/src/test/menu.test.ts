import { describe, expect, it } from "vitest";
import { placeMenu } from "../components/ui";

// A button 32×32 with its right edge at x=1200; the menu is 190×120; the window 1280×800.
const button = (top: number, right = 1200) => ({ top, bottom: top + 32, left: right - 32, right, width: 32, height: 32, x: right - 32, y: top, toJSON: () => ({}) }) as DOMRect;
const menu = { width: 190, height: 120 };
const view = { width: 1280, height: 800 };

describe("where an open menu goes", () => {
  it("opens below its button, right edges lined up", () => {
    expect(placeMenu(button(100), menu, view)).toEqual({ top: 136, left: 1010, side: "below" });
  });

  it("opens above when there isn't room below", () => {
    expect(placeMenu(button(700), menu, view)).toEqual({ top: 576, left: 1010, side: "above" });
  });

  it("stays inside the window when there's room neither below nor above", () => {
    const place = placeMenu(button(60), { width: 190, height: 760 }, view);
    expect(place.top).toBe(800 - 8 - 760); // as low as it can be, with its bottom inside the window
    expect(place.top).toBeGreaterThanOrEqual(8);
  });

  it("is pushed in from the right edge", () => {
    expect(placeMenu(button(100, 1278), menu, view).left).toBe(1280 - 8 - 190);
  });

  it("is pushed in from the left edge on a narrow window", () => {
    expect(placeMenu(button(100, 60), menu, { width: 400, height: 800 }).left).toBe(8);
  });

  it("starts at the left edge when the window is narrower than the menu", () => {
    expect(placeMenu(button(100, 150), menu, { width: 180, height: 800 }).left).toBe(8);
  });
});
