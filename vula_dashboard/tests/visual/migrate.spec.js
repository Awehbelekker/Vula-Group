// migrate.spec.js — screens moved onto the redesign's components (UX redesign Phase F). UI_V2=1.
// Each screen GATES on axe (serious/critical = fail) and on no sideways scroll at 390px; the
// flag-off look of the same screens is baseline.spec.js's job.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

test.skip(process.env.UI_V2 !== "1", "the redesign runs with UI_V2=1");
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

const INBOX = { name: "inbox", role: "owner", nav: "Inbox", ready: "Waiting on you (3)" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`inbox ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, INBOX, { viewport, theme });
      await check(page, ti, "inbox", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: /Can you do a platter|Let me check with the team/ }).first().click();
      await expect(page.getByText("Budget about R2,500.", { exact: true })).toBeVisible();
      await check(page, ti, "inbox-thread", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("inbox: search and chips filter the list; back returns to it on a phone", async ({ page }) => {
  await openScreen(page, INBOX, { viewport: VIEWPORTS.mobile, theme: "light" });
  const rows = page.locator(".vin-list [role=listitem]");
  await expect(rows).toHaveCount(8);
  await page.getByRole("button", { name: /You took over/ }).click();
  await expect(rows).toHaveCount(1);
  await page.getByRole("button", { name: /You took over/ }).click();
  await page.getByRole("button", { name: /Waiting on you/ }).click();
  await expect(rows).toHaveCount(3);
  await page.getByRole("button", { name: /Waiting on you/ }).click();
  await page.getByRole("searchbox").fill("0110");
  await expect(rows).toHaveCount(1);
  await rows.first().getByRole("button").click();
  await expect(page.locator(".vin-list")).toBeHidden();
  await page.getByRole("button", { name: "Back to chats" }).click();
  await expect(page.locator(".vin-list")).toBeVisible();
});
