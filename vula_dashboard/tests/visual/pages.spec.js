// pages.spec.js — Storefront › Pages (the Puck page builder) on the redesign's components (UX
// redesign Phase F, 2026-10-20). UI_V2=1: the page list GATES on axe and on no sideways scroll at
// 390px; the editor's own chrome (toolbar, SEO, history, refine) is checked with the Puck canvas
// itself left out of axe (it renders the tenant's real site). Requests match the current look's.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

const V2 = process.env.UI_V2 === "1";
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ") + (process.env.AXE_DEBUG ? n.failureSummary : "")).join(" | ")}`);

async function check(page, ti, name, vp, viewport, { editor = false } = {}) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  let axe = new AxeBuilder({ page }).include(".v2-content");
  if (editor) axe = axe.exclude(".vpg-editor");
  expect(bad(await axe.analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}
function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (r.method() === method && re.test(u.pathname)) seen.push({ path: u.pathname, body: r.postDataJSON?.() ?? null }); });
  return seen;
}

const LIST = { name: "pages", role: "owner", nav: "Storefront", ready: "Winter specials" };

test.describe("redesign: storefront pages", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`list ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, LIST, { viewport, theme });
        await check(page, ti, "pg-list", `${vp}-${theme}`, viewport);
        expect(errors).toEqual([]);
      });
      test(`editor ${vp} ${theme}`, async ({ page }, ti) => {
        await openScreen(page, LIST, { viewport, theme });
        await page.getByRole("button", { name: /^About us/ }).click();
        await expect(page.getByRole("heading", { name: "About us" })).toBeVisible();
        await expect(page.locator(".vpg-editor")).toBeVisible();
        await check(page, ti, "pg-editor", `${vp}-${theme}`, viewport, { editor: true });
      });
    }
  }

  test("list: reorder, rename, delete (asks first) and a new page send the same requests", async ({ page }) => {
    await openScreen(page, LIST, { viewport: VIEWPORTS.desktop, theme: "light" });
    const posts = capture(page, "POST", /\/pages\//);
    const puts = capture(page, "PUT", /\/pages\//);
    const dels = capture(page, "DELETE", /\/pages\//);
    await page.getByRole("button", { name: "Move Winter specials up" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/reorder"))?.body).toEqual({ order: ["winter-specials", "about"] });
    await page.getByRole("button", { name: "More for About us" }).click();
    await page.getByRole("dialog", { name: "About us" }).getByRole("button", { name: "Rename" }).click();
    const prompt = page.getByRole("dialog").filter({ hasText: "New page name:" });
    await prompt.getByRole("textbox").fill("Our story");
    await prompt.getByRole("button", { name: /OK|Save|Yes/ }).click();
    await expect.poll(() => puts.find((p) => p.path.endsWith("/pages/about"))?.body).toEqual({ title: "Our story", status: "published" });
    await page.getByRole("button", { name: "More for Winter specials" }).click();
    await page.getByRole("dialog", { name: "Winter specials" }).getByRole("button", { name: "Delete" }).click();
    await page.getByRole("dialog").filter({ hasText: 'Delete the page "Winter specials"?' }).getByRole("button", { name: "Yes" }).click();
    await expect.poll(() => dels.map((d) => d.path.split("/").pop())).toEqual(["winter-specials"]);
    await page.getByRole("button", { name: "New page" }).click();
    const sheet = page.getByRole("dialog", { name: "New page" });
    await sheet.getByLabel(/^Page name/).fill("Gift boxes");
    await sheet.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("heading", { name: "Gift boxes" })).toBeVisible();
  });

  test("editor: save draft, SEO, history restore (asks first) and refine", async ({ page }) => {
    await openScreen(page, LIST, { viewport: VIEWPORTS.desktop, theme: "light" });
    const puts = capture(page, "PUT", /\/pages\//);
    const posts = capture(page, "POST", /\/pages\//);
    await page.getByRole("button", { name: /^About us/ }).click();
    await page.getByRole("group", { name: "Page SEO" }).getByLabel(/^Title/).fill("About the deli");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect.poll(() => puts[0]?.body?.seo?.title).toBe("About the deli");
    expect(puts[0].body.status).toBe("draft");
    await expect(page.getByRole("status").filter({ hasText: "Draft saved" })).toBeVisible();

    await page.getByRole("button", { name: "Version history" }).click();
    await page.getByRole("dialog", { name: "Version history" }).getByRole("button", { name: "Restore as draft" }).click();
    await page.getByRole("dialog").filter({ hasText: "Restore this version?" }).getByRole("button", { name: /OK|Yes|Confirm/ }).click();
    await expect.poll(() => posts.some((p) => p.path.endsWith("/versions/pv-1/restore"))).toBe(true);

    await page.getByRole("button", { name: "Refine with AI" }).click();
    const refine = page.getByRole("region", { name: "Refine with AI" });
    await refine.getByLabel("What would you like to change?").fill("make the headline punchier");
    await refine.getByRole("button", { name: "Send" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/ai-refine"))?.body?.instruction).toBe("make the headline punchier");
    await expect(refine.getByText("Done: take a look")).toBeVisible();
  });
});

test("current look: the page builder still loads on the shared hook", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...LIST, ready: "Storefront pages" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText("Winter specials")).toBeVisible();
  await page.getByText("About us").click();
  await expect(page.getByText("💾 Save draft")).toBeVisible();
  // the homepage starter builds its data with the hook file's norm()
  await page.getByRole("button", { name: "← Pages" }).click();
  await page.getByRole("button", { name: "✎ Start from template" }).click();
  await expect(page.getByText("/home")).toBeVisible();
});
