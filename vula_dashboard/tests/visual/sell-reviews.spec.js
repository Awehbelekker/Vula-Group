// sell-reviews.spec.js — Sell › Reviews, Sell › Sales reports and "Send to WhatsApp catalogue" on
// Products (23 Nov 2026). Each screen GATES on axe (serious/critical = fail) and no sideways
// scroll at 390px; the Google link and the catalogue send are checked against what goes to the API.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => { if (r.method() === method && re.test(new URL(r.url()).pathname)) seen.push(r.postDataJSON?.() ?? null); });
  return seen;
}

const REVIEWS = { name: "reviews", role: "owner", nav: "Sell", tab: "Reviews", ready: "Google review link" };
const SALES = { name: "salesreports", role: "owner", nav: "Sell", tab: "Sales reports", ready: "Sales Reports" };
const PRODUCTS = { name: "products", role: "owner", nav: "Sell", tab: "Products", ready: "Everything you sell, by category" };

test.describe("redesign", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`reviews ${vp} ${theme}`, async ({ page }, ti) => {
        await openScreen(page, REVIEWS, { viewport, theme });
        await expect(page.getByText("4.3", { exact: true })).toBeVisible();
        await expect(page.getByText("Thandi Nkosi")).toBeVisible();
        await expect(page.getByText("Follow up").first()).toBeVisible();      // the 2-star one
        await check(page, ti, "reviews", `${vp}-${theme}`, viewport);
      });
    }
  }

  test("the Google review link is checked and saved", async ({ page }) => {
    const posts = capture(page, "POST", /\/admin\/reviews\/settings$/);
    await openScreen(page, REVIEWS, { viewport: VIEWPORTS.mobile, theme: "light" });
    const box = page.getByLabel("Link");
    await box.fill("https://bit.ly/my-shop");
    await page.getByRole("button", { name: "Save link" }).click();
    await expect(page.getByText("That isn't a Google review link.")).toBeVisible();
    await box.fill("https://g.page/r/CdemoShop/review");
    await page.getByRole("button", { name: "Save link" }).click();
    await expect(page.getByText("Customers who give 4 or 5 stars get this link.")).toBeVisible();
    expect(posts.at(-1)).toEqual({ google_review_url: "https://g.page/r/CdemoShop/review" });
  });

  test("sales reports open under Sell", async ({ page }, ti) => {
    await openScreen(page, SALES, { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByText("Top Products").first()).toBeVisible();
    await check(page, ti, "salesreports", "desktop-light", VIEWPORTS.desktop);
  });

  test("products can be sent to the WhatsApp catalogue", async ({ page }) => {
    const posts = capture(page, "POST", /\/admin\/meta-catalog\/sync$/);
    await openScreen(page, PRODUCTS, { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("button", { name: "Send to WhatsApp catalogue" }).click();
    await expect(page.getByText("8 products sent to your WhatsApp catalogue.", { exact: false })).toBeVisible();
    expect(posts.length).toBe(1);
  });
});
