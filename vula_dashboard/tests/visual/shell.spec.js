// shell.spec.js — the redesigned shell (2026-10-17, UX redesign Phase D). Runs only with
// UI_V2=1. For owner, a limited staff login and a sales rep, at phone / tablet / desktop in
// light and dark: Home screenshotted, plus the More sheet (phone), the rail opened (tablet) and
// the search palette (desktop). The shell chrome (sidebar, top bar, toolbar, sheets) GATES on
// zero serious/critical axe violations; page content still being migrated is not in scope here.
// What each role may see is asserted from the nav itself (gating unchanged from navConfig).
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

test.skip(process.env.UI_V2 !== "1", "the redesigned shell runs with UI_V2=1");
const out = (ti, name) => path.join(ti.project.testDir, ".results", "shell", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

const ROLES = {
  owner: { ready: "Won in", sees: ["Home", "Today", "Inbox", "All activity", "Money", "Team", "Settings"], hidden: [] },
  staff: { ready: "Chats waiting on you", sees: ["Home", "Today", "Inbox", "All activity", "People", "Sell"], hidden: ["Money", "Team", "Settings", "Marketing"] },
  rep: { ready: "Reminders", sees: ["Home", "Today", "My Work"], hidden: ["Money", "Team", "Settings", "Inbox", "All activity"] },
};

for (const [role, R] of Object.entries(ROLES)) {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`shell ${role} ${vp} ${theme}`, async ({ page }, ti) => {
        const screen = { name: "home", role, nav: null, ready: R.ready };
        const errors = await openScreen(page, screen, { viewport, theme });
        await page.screenshot({ path: out(ti, `shell-${role}-${vp}-${theme}.png`), fullPage: false, animations: "disabled" });
        expect(bad(await new AxeBuilder({ page }).include(".v2-top").include(vp === "mobile" ? ".vui-toolbar" : ".v2-side").analyze())).toEqual([]);

        // what this role's nav offers (desktop sidebar / tablet rail / phone More sheet)
        let labels;
        if (vp === "mobile") {
          await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "More" }).click();
          const sheet = page.getByRole("dialog", { name: "Menu" });
          await sheet.waitFor();
          await page.screenshot({ path: out(ti, `shell-${role}-${vp}-${theme}-more.png`), animations: "disabled" });
          expect(bad(await new AxeBuilder({ page }).include(".vui-sheet").analyze())).toEqual([]);
          labels = await sheet.locator(".vui-row-title").allTextContents();
          await page.keyboard.press("Escape");
        } else if (vp === "tablet") {
          labels = await page.locator(".v2-side.rail .v2-nav-item").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
          await page.getByRole("button", { name: "Open the menu" }).click();
          await page.screenshot({ path: out(ti, `shell-${role}-${vp}-${theme}-menu.png`), animations: "disabled" });
          await page.getByRole("button", { name: "Close the menu" }).click();
        } else {
          labels = await page.locator(".v2-side .v2-nav-lb").allTextContents();
          await page.keyboard.press("Control+k");
          await page.getByRole("combobox").fill("in");
          await page.screenshot({ path: out(ti, `shell-${role}-${vp}-${theme}-search.png`), animations: "disabled" });
          expect(bad(await new AxeBuilder({ page }).include(".vui-palette").analyze())).toEqual([]);
          await page.keyboard.press("Escape");
        }
        for (const l of R.sees) expect(labels, `${role} should see ${l}`).toContain(l);
        for (const l of R.hidden) expect(labels, `${role} must not see ${l}`).not.toContain(l);
        if (vp === "mobile") {
          const sw = await page.evaluate(() => document.documentElement.scrollWidth);
          expect(sw).toBeLessThanOrEqual(viewport.width);
          await expect(page.locator("button.vshell-burger")).toHaveCount(0);   // no burger in the redesign
        }
        expect(errors).toEqual([]);
      });
    }
  }
}

test("Add offers only what the role may use; a sub-tab opens straight from search", async ({ page }) => {
  await openScreen(page, { name: "home", role: "owner", nav: null, ready: "Won in" }, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "Add" }).click();
  const add = page.getByRole("dialog", { name: "Add" });
  await expect(add.locator(".vui-row-title")).toContainText(["New quote or invoice", "Add an expense"]);
  await page.keyboard.press("Escape");
  await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "Search" }).click();
  await page.getByRole("combobox").fill("invoices");
  await page.keyboard.press("Enter");
  await expect(page.locator('[role=tab][data-tab="invoices"][aria-selected="true"]')).toBeVisible();   // the Money section tab (the list has its own "Invoices" type tab)
  await expect(page.locator(".v2-title")).toHaveText("Money");
});

test("Master can put one business back on the old design", async ({ page }) => {
  await openScreen(page, { name: "master", role: "master", nav: "Master", ready: "need attention" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("row", { name: /Harbour & Vine Deli/ }).click();   // a tenant opens in a sheet
  const box = page.getByRole("checkbox", { name: /New dashboard design/ });
  await expect(box).toBeChecked();             // on for every business since migration 217
  await box.uncheck();
  const patch = page.waitForRequest((r) => r.method() === "PATCH" && /\/v1\/master\/tenants\/[^/]+$/.test(r.url()));
  await page.getByRole("button", { name: "Save changes" }).click();
  expect((await patch).postDataJSON()).toMatchObject({ ui_v2: false });
});
