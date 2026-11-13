// prospects.spec.js — Master › Prospects (13 Nov 2026): scan a business's website before it's a
// client, read the report and the pitch, then make it a tenant (the new-tenant form, filled in
// with the website). Phone, tablet and desktop: axe clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { VIEWPORTS, openScreen } from "./screens.js";

const MASTER = { name: "master", role: "master", nav: "Master", ready: "need attention" };
const serious = async (page) => (await new AxeBuilder({ page }).include('section[aria-label="Prospects"]').analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`a prospect's report, pitch and "make them a tenant" — ${vp}`, async ({ page }) => {
    await openScreen(page, MASTER, { viewport, theme: "light" });
    await page.getByRole("tab", { name: "Prospects" }).click();
    const list = page.getByRole("list", { name: "Prospects scanned" });
    await expect(list.getByText("Scanning…")).toBeVisible();                     // ShopEazy, still reading
    await list.getByRole("button", { name: /Kelp Gear/ }).click();
    await expect(page.getByText("6 products, 6 with a price", { exact: true })).toBeVisible();
    await expect(page.getByText("No delivery or shipping policy on the website", { exact: true })).toBeVisible();
    await expect(page.getByText(/Kelp Classic Day Bag — R\s?1\s?200–R\s?2\s?400, 15 options, 9 photos/)).toBeVisible();
    await expect(page.getByLabel("The pitch")).toHaveValue(/^Kelp Gear: what Vula found on www\.kelpgearshop\.com/);
    expect(await serious(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

    await page.getByRole("button", { name: "Make them a tenant" }).click();
    await expect(page.getByRole("tab", { name: "Tenants" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('input[value="kelp-gear"]')).toBeVisible();
    await expect(page.locator('input[value="https://www.kelpgearshop.com"]')).toBeVisible();
  });
}

test("scanning a website adds it to the list", async ({ page }) => {
  await openScreen(page, MASTER, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("tab", { name: "Prospects" }).click();
  const form = page.getByRole("form", { name: "Scan a prospect" });
  await form.getByLabel("Website").fill("seedleme.com");
  await form.getByLabel("Business name (optional)").fill("SeedleMe");
  await form.getByRole("button", { name: "Scan" }).click();
  await expect(page.getByRole("list", { name: "Prospects scanned" }).getByRole("button", { name: /SeedleMe/ })).toBeVisible();
  await expect(page.getByText("Reading the website… this takes a minute or two.")).toBeVisible();
});
