// hq.spec.js — Master HQ's business screens on the redesign's components (UX redesign Phase F,
// 2026-10-20): Onboard Client, Signups, Subscriptions and Training KB. UI_V2=1: each GATES on axe
// and on no sideways scroll at 390px; the requests match the current look's.
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

const HQ = (nav, ready, zone = "Vula's Business") => ({ name: nav.toLowerCase(), role: "master", nav, ready, zone });
const SCREENS = [
  ["onboard", HQ("Onboard Client", "Set a client up on Vula")],
  ["signups", HQ("Signups", "Ridgeline Renovations")],
  ["subscriptions", HQ("Subscriptions", "Active paid subscribers")],
  ["training", HQ("Training KB", "JBCC contracts and payment certificates", null)],
];

test.describe("redesign: master HQ business screens", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [name, screen] of SCREENS) {
    for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
      for (const theme of THEMES) {
        test(`${name} ${vp} ${theme}`, async ({ page }, ti) => {
          const errors = await openScreen(page, screen, { viewport, theme });
          await check(page, ti, `hq-${name}`, `${vp}-${theme}`, viewport);
          expect(errors).toEqual([]);
        });
      }
    }
  }

  test("onboard: the wizard sends the same body and shows the workspace", async ({ page }, ti) => {
    await openScreen(page, SCREENS[0][1], { viewport: VIEWPORTS.mobile, theme: "light" });
    await page.getByRole("button", { name: "Start" }).click();
    await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled();
    await page.getByRole("radio", { name: /^Growth/ }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByLabel("Company name").fill("Kalk Bay Tiles");
    await page.getByRole("combobox", { name: "Industry", exact: true }).selectOption("Retail & E-commerce");
    await page.getByLabel("Contact's full name").fill("Ayanda Zulu");
    await page.getByLabel("Work email").fill("ayanda@kbtiles.example.test");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("checkbox", { name: "Invoicing and cashflow is manual and slow" }).check();
    await page.getByRole("button", { name: "Continue" }).click();
    await check(page, ti, "hq-onboard-review", "mobile-light", VIEWPORTS.mobile);
    const activate = page.getByRole("button", { name: "Activate the free trial" });
    await expect(activate).toBeDisabled();
    await page.getByRole("checkbox", { name: /They agree to the Vula Group Terms/ }).check();
    const req = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/v1/onboard");
    await activate.click();
    expect((await req).postDataJSON()).toEqual({ company_name: "Kalk Bay Tiles", industry: "Retail & E-commerce", staff_count: null, contact_name: "Ayanda Zulu",
      email: "ayanda@kbtiles.example.test", whatsapp: null, pain_points: ["Invoicing and cashflow is manual and slow"], plan: "growth" });
    await expect(page.getByText("https://kalk-bay-tiles.vula.site")).toBeVisible();
    await expect(page.getByRole("link", { name: /Complete the subscription on PayFast/ })).toHaveAttribute("href", "https://payfast.example.test/sub/kbt");
  });

  test("signups, subscriptions and training: filters, details and a force re-seed that asks first", async ({ page }) => {
    await openScreen(page, SCREENS[1][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: "Business" }).click();
    await expect(page.getByRole("list", { name: "Signups" }).getByText("Ridgeline Renovations")).toHaveCount(0);
    await page.getByRole("button", { name: /Harbour & Vine Deli/ }).click();
    await expect(page.getByText("owner@harbourvine.example.test")).toBeVisible();

    await openScreen(page, SCREENS[2][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByRole("heading", { name: /Expired: needs reactivation/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Expiring in 3 days/ })).toBeVisible();

    await openScreen(page, SCREENS[3][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    const seeds = [];
    page.on("request", (r) => { if (r.method() === "POST" && r.url().includes("/seed")) seeds.push(new URL(r.url()).search); });
    await page.getByRole("button", { name: "Force re-seed" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    expect(seeds).toEqual([]);
    await page.getByRole("button", { name: "Force re-seed" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Force re-seed" }).click();
    await expect.poll(() => seeds).toEqual(["?force=true"]);
    await expect(page.getByText("Seeding started: about two minutes.")).toBeVisible();
  });
});

test("current look: the HQ business screens still load on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...SCREENS[1][1], ready: "Client Signups" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText("Sam Petersen")).toBeVisible();
});
