// choices.spec.js — a product's choices on the same price (colour, trim, nose and tail style…,
// migration 267, 13 Nov 2026): added in the product editor and saved as [{name, values}]. Phone
// and desktop: axe clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { VIEWPORTS, openScreen } from "./screens.js";

const PRODUCTS = { name: "products", role: "owner", nav: "Sell", tab: "Products", ready: "Sourdough loaf" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`a product's choices are added and saved — ${vp}`, async ({ page }) => {
    await openScreen(page, PRODUCTS, { viewport, theme: "light" });
    const saved = [];
    page.on("request", (req) => {
      if (req.method() === "PATCH" && /\/admin\/products\/p-02$/.test(new URL(req.url()).pathname)) saved.push(req.postDataJSON());
    });
    await page.getByRole("button", { name: "Edit Sourdough loaf" }).click();
    await page.getByRole("button", { name: "+ Add a choice" }).click();
    await page.getByLabel("Choice name").fill("Colour");
    await page.getByLabel("Values, separated by commas").fill("Olive Green, Navy Blue ,, Black");
    await page.getByRole("button", { name: "Save choices" }).click();
    await expect.poll(() => saved.length).toBe(1);
    expect(saved[0]).toEqual({ choices: [{ name: "Colour", values: ["Olive Green", "Navy Blue", "Black"] }] });
    // the new section only: the rest of the product editor predates this and isn't changed here
    const serious = (await new AxeBuilder({ page }).include('[role="group"][aria-label="Choices"]').analyze()).violations
      .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);
    expect(serious).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
  });
}
