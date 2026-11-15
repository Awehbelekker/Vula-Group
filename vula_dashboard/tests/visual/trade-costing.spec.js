// trade-costing.spec.js — Estimating › Costing by trade (15 Nov 2026). The tab shows only where the
// business has the trade_costing switch on; an estimate shows each line's price, where it came from
// and when, what isn't priced or has no figure, and the business's own rate; the Excel downloads;
// an owner confirms or changes a build-up, staff can't. Axe-clean with no sideways scroll at all
// three widths.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, openScreen } from "./screens.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "trade-costing", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

const MODULES = ["projects", "estimating", "ai_draft"];
const SCREEN = (role = "owner", features = ["trade_costing"]) => ({
  name: "tradecosting", role, nav: "Estimating", tab: "Costing by trade", ready: "Costing by trade",
  modules: MODULES, features });

async function check(page, ti, name, viewport) {
  await page.screenshot({ path: out(ti, `${name}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

async function runEstimate(page) {
  await page.getByLabel("Item of work").first().fill("one-brick wall (220 mm)");
  await page.getByLabel("Size").first().fill("6 x 4");
  await page.getByRole("button", { name: "Add item" }).click();
  await page.getByLabel("Item of work").nth(1).fill("plaster to brick walls");
  await page.getByLabel("Size").nth(1).fill("6 x 4 x 2");
  await page.getByLabel("Not included").fill("painting");
  await page.getByRole("button", { name: "Work it out" }).click();
  await page.getByText("R 14 220.01").first().waitFor();
}

test.describe("costing by trade", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    test(`estimate ${vp}`, async ({ page }, ti) => {
      const errors = await openScreen(page, SCREEN(), { viewport, theme: "light" });
      await runEstimate(page);
      const content = page.locator(".v2-content");
      await expect(content).toContainText("Materials R 11 220.01 · Labour R 3 000.00");
      await expect(content).toContainText("What you paid, 2026-11-09");
      await expect(content).toContainText("market now R 114.99");
      await expect(content).toContainText("Market price, 2026-11-12");
      await expect(content).toContainText("Waiting for an owner or manager");
      await expect(content).toContainText("Your own rate: R 554.56/m² (your HPC Bokaap BOQ, 26 Oct 2026)");
      await expect(content).toContainText("Needs a figure: Wall ties");
      await expect(content).toContainText("Not priced (no price on file or checked online): Plaster sand");
      await expect(content).toContainText("Not included, as asked: painting");
      await check(page, ti, `estimate-${vp}`, viewport);
      expect(errors).toEqual([]);
    });

    test(`build-ups ${vp}`, async ({ page }, ti) => {
      const errors = await openScreen(page, SCREEN(), { viewport, theme: "dark" });
      await page.getByRole("tab", { name: /Build-ups/ }).click();
      await page.getByText("Plastering").first().waitFor();
      await check(page, ti, `buildups-${vp}`, viewport);
      expect(errors).toEqual([]);
    });
  }

  test("the estimate sends the person's own figures and the known build-up", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    const sent = page.waitForRequest((r) => r.url().endsWith("/admin/estimates") && r.method() === "POST");
    await runEstimate(page);
    const body = (await sent).postDataJSON();
    expect(body.items).toEqual([
      { item: "one-brick wall", quantity_expr: "6 x 4", unit: "m2", spec: { thickness: "220 mm" } },
      { item: "plaster to brick walls", quantity_expr: "6 x 4 x 2", unit: "m2", spec: {} },
    ]);
    expect(body.exclusions).toEqual(["painting"]);
  });

  test("download the Excel breakdown", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await runEstimate(page);
    const dl = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download Excel" }).click();
    expect((await dl).suggestedFilename()).toBe("Estimate_2026-11-15.xlsx");
  });

  test("an owner confirms and changes a build-up", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /Build-ups/ }).click();
    const plaster = page.locator(".vula-card, .v2-card, section").filter({ hasText: "plaster to brick walls" }).last();
    await page.getByText("plaster to brick walls").first().waitFor();
    await plaster.getByRole("button", { name: "Change" }).click();
    await page.getByLabel("Plaster cement: waste %").fill("10");
    const sent = page.waitForRequest((r) => /\/buildups\/bu-plaster\/confirm$/.test(r.url()));
    await page.getByRole("button", { name: "Save and confirm" }).click();
    expect((await sent).postDataJSON()).toEqual({ changes: [{ component: "cement", waste_pct: 10 }] });
    await expect(page.locator(".v2-content")).toContainText("Plaster cement");
    await expect(page.locator(".v2-content")).toContainText("+ 10% waste");
  });

  test("staff without access to it don't get the tab", async ({ page }) => {
    await openScreen(page, { ...SCREEN("staff"), nav: undefined, tab: undefined, ready: "Overview" }, { viewport: VIEWPORTS.desktop, theme: "light" }).catch(() => null);
    // Staff in the fixture have no access to Costing by trade at all — the tab isn't offered.
    await expect(page.getByRole("tab", { name: "Costing by trade" })).toHaveCount(0);
  });

  test("research a build-up", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /Build-ups/ }).click();
    const form = page.getByRole("form", { name: "Research a build-up" });
    await form.getByLabel("Item of work").fill("floor tiling");
    await form.getByRole("button", { name: "Research it" }).click();
    await page.getByText("floor tiling").first().waitFor();
    await expect(page.locator(".v2-content")).toContainText("Needs a figure: Grout");
  });

  // phase 4: an estimate for a project, compared with what was paid on it
  async function compare(page) {
    await page.getByLabel("Item of work").first().fill("one-brick wall (220 mm)");
    await page.getByLabel("Size").first().fill("6 x 4");
    await page.getByLabel("Project").selectOption("Bay Events catering");
    await page.getByRole("button", { name: "Work it out" }).click();
    await page.getByRole("button", { name: "Compare with what was paid" }).click();
    await page.getByText("compared with what was paid").first().waitFor();
  }

  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    test(`compare with what was paid ${vp}`, async ({ page }, ti) => {
      const errors = await openScreen(page, SCREEN(), { viewport, theme: vp === "tablet" ? "dark" : "light" });
      await compare(page);
      const content = page.locator(".v2-content");
      await expect(content).toContainText("Project Bay Events catering · estimated R 14 220.01 · paid on what it covers R 13 109.00");
      await expect(content).toContainText("progress only");
      await expect(content).toContainText("+18.3%");
      await expect(content).toContainText("Used by one-brick wall and plaster to brick walls — total only");
      await expect(content).toContainText("Couldn't tell which component: River sand (Mortar (building) sand or Plaster sand)");
      await expect(content).toContainText("1 lines, R 3 000.00");
      await check(page, ti, `actuals-${vp}`, viewport);
      expect(errors).toEqual([]);
    });
  }

  test("the estimate carries the project it's for", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    const sent = page.waitForRequest((r) => r.url().endsWith("/admin/estimates") && r.method() === "POST");
    await compare(page);
    expect((await sent).postDataJSON().project).toBe("Bay Events catering");
  });

  test("finished work: Vula suggests, the owner accepts, the build-up changes", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await compare(page);
    await page.getByRole("button", { name: "This work is finished — learn from it" }).click();
    await page.getByRole("button", { name: "Yes, it's finished" }).click();
    const list = page.getByRole("region", { name: "Suggested build-up changes" });
    await expect(list).toContainText("waste 5% → 24%");
    await expect(page.locator(".v2-content")).not.toContainText("progress only");
    await expect(page.locator(".v2-content")).toContainText("Suggested change");
    const sent = page.waitForRequest((r) => /\/buildup-suggestions\/sg-1\/accept$/.test(r.url()));
    await list.getByRole("button", { name: "Accept" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Accept" }).click();
    await sent;
    await expect(list).toContainText("Accepted");
    await page.getByRole("tab", { name: /Build-ups/ }).click();
    await expect(page.locator(".v2-content")).toContainText("104 each + 24% waste");
  });

  test("a suggestion can be dismissed from the build-ups tab", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await compare(page);
    await page.getByRole("button", { name: "This work is finished — learn from it" }).click();
    await page.getByRole("button", { name: "Yes, it's finished" }).click();
    await page.getByRole("region", { name: "Suggested build-up changes" }).waitFor();
    await page.getByRole("tab", { name: /Build-ups/ }).click();
    const list = page.getByRole("region", { name: "Suggested build-up changes" });
    await expect(page.locator(".v2-content")).toContainText("Suggested change");
    await list.getByRole("button", { name: "Dismiss" }).click();
    await expect(page.getByRole("region", { name: "Suggested build-up changes" })).toHaveCount(0);
  });

  test("hidden where the switch is off", async ({ page }) => {
    await openScreen(page, { ...SCREEN("owner", []), tab: "Quick Cost", ready: "Cost breakdown" },
      { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByRole("tab", { name: "Costing by trade" })).toHaveCount(0);
  });
});
