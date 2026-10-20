// estimating.spec.js — Estimating › Quick Cost, QS Pro, Takeoff and Draft on the redesign's
// components (UX redesign Phase F, 2026-10-20). UI_V2=1: each screen GATES on axe and on no
// sideways scroll at 390px (wide tables scroll inside their own region), the maths shows the same
// figures as the current look, and the requests match.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

const V2 = process.env.UI_V2 === "1";
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ") + (process.env.AXE_DEBUG ? n.failureSummary : "")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

const MODULES = ["projects", "estimating", "ai_draft"];
const ES = (tab, ready) => ({ name: tab.toLowerCase(), role: "owner", nav: "Estimating", tab, ready, modules: MODULES });
const SCREENS = [
  ["quickcost", ES("Quick Cost", "Cost breakdown")],
  ["qspro", ES("QS Pro", "Compare with suppliers' quotes")],
  ["takeoff", ES("Takeoff", "Bill of quantities (demo)")],
  ["draft", ES("AI Draft", "Nothing drafted yet")],
];

test.describe("redesign: estimating", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [name, screen] of SCREENS) {
    for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
      for (const theme of THEMES) {
        test(`${name} ${vp} ${theme}`, async ({ page }, ti) => {
          const errors = await openScreen(page, screen, { viewport, theme });
          await check(page, ti, `es-${name}`, `${vp}-${theme}`, viewport);
          expect(errors).toEqual([]);
        });
      }
    }
  }

  test("quick cost: the same total as the current look's maths", async ({ page }) => {
    await openScreen(page, SCREENS[0][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    // fitout_standard mid 9 250 × 1.05 (WC) × 500 m² = 4 856 250; +10% contingency = 5 341 875
    await expect(page.getByText("Construction total")).toBeVisible();
    const cost = page.getByRole("group", { name: "Cost breakdown" });
    await expect(cost.getByText("R 5 341 875")).toBeVisible();
    await page.getByLabel("Floor area (m²)").fill("100");
    await expect(cost.getByText("R 1 068 375")).toBeVisible();
    await page.getByRole("switch", { name: "Include VAT (15%)" }).click();
    await expect(cost.getByText("Total excl. VAT", { exact: true })).toBeVisible();
  });

  test("qs pro: a line from the library adds up; removing it drops it", async ({ page }) => {
    await openScreen(page, SCREENS[1][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByLabel("Search materials").fill("Palisade");
    await page.getByRole("button", { name: "Add Palisade fencing (erected)" }).click();
    const lines = page.getByRole("region", { name: "Estimate lines" });
    await lines.getByLabel("Quantity for Palisade fencing (erected)").fill("10");
    // mid (850+1600)/2 = 1 225 × 10 = 12 250 net; +15% = 14 088 (rounded)
    await expect(lines.getByText("R 12 250")).toBeVisible();
    await expect(lines.getByText("R 14 088")).toBeVisible();
    await page.getByRole("button", { name: "Remove Palisade fencing (erected)" }).click();
    await expect(lines.getByText("R 12 250")).toHaveCount(0);
  });

  test("takeoff: rates and an upload call the same endpoints", async ({ page }) => {
    await openScreen(page, SCREENS[2][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: "Market rates" }).click();
    await expect(page.getByText("Partition, single skin 70mm")).toBeVisible();
    const up = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/takeoff/upload");
    await page.locator('input[type="file"]').setInputFiles({ name: "plans.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
    expect(new URL((await up).url()).searchParams.get("tenant_id")).toBeTruthy();
    await expect(page.getByText("plans.pdf: Queued — waiting to start…")).toBeVisible();
  });

  test("draft: the same body; the document shows and copies", async ({ page }) => {
    await openScreen(page, SCREENS[3][1], { viewport: VIEWPORTS.mobile, theme: "light" });
    await page.getByLabel(/^Brief/).fill("Fee proposal for the spring gala marquee");
    await page.getByLabel(/^Project value/).fill("1800000");
    const req = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/v1/draft/generate"));
    await page.getByRole("button", { name: "Draft it" }).click();
    expect((await req).postDataJSON()).toMatchObject({ document_type: "fee_proposal", brief: "Fee proposal for the spring gala marquee", project_value: 1800000 });
    await expect(page.getByRole("region", { name: "Drafted document" }).or(page.getByLabel("Drafted document"))).toContainText("R 186 000 excl. VAT");
  });
});

test("current look: the Estimating tabs still load on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...SCREENS[0][1], ready: "Cost Breakdown" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText(/^R 5.341.875$/).first()).toBeVisible();
  await page.getByRole("tab", { name: /QS Pro$/ }).first().click();
  await expect(page.getByText("VULA QS PRO")).toBeVisible();
  await page.getByRole("tab", { name: /Takeoff$/ }).first().click();
  await expect(page.getByText("VULA TAKEOFF")).toBeVisible();
  await page.getByRole("tab", { name: /AI Draft$/ }).first().click();
  await expect(page.getByText("Vula Draft")).toBeVisible();
});
