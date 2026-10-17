// uikit.spec.js — the shared components on #/ui-kit (2026-10-17, UX redesign Phase C).
//
// Unlike baseline/a11y (which record today's screens as they are), this one GATES: the new
// components must have no serious or critical axe violations and must not scroll the page
// sideways at 390px, in light and dark. Screenshots go to tests/visual/.results/uikit/
// (UPDATE_BASELINE=1 → tests/visual/baseline/uikit/).
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { installMockApi, seedSession } from "./mockApi.js";
import { VIEWPORTS, THEMES } from "./screens.js";
import { NOW_ISO } from "./fixtures/demo.js";

const outDir = (ti) => path.join(ti.project.testDir, process.env.UPDATE_BASELINE === "1" ? "baseline" : ".results", "uikit");

async function openKit(page, { viewport, theme }) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "master" });
  await seedSession(page, { role: "master", theme });
  await page.goto("/#/ui-kit");
  await page.getByRole("heading", { name: "UI kit" }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
  return errors;
}

test.describe.configure({ mode: "parallel" });

for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`ui-kit ${vpName} ${theme}`, async ({ page }, ti) => {
      const errors = await openKit(page, { viewport, theme });
      await page.screenshot({ path: path.join(outDir(ti), `uikit-${vpName}-${theme}.png`), fullPage: true, animations: "disabled", caret: "hide" });

      const axe = await new AxeBuilder({ page }).analyze();
      const bad = axe.violations.filter((v) => ["serious", "critical"].includes(v.impact))
        .map((v) => `${v.id} (${v.nodes.length}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
      expect(bad, "serious/critical axe violations").toEqual([]);

      if (vpName === "mobile") {
        const sw = await page.evaluate(() => document.documentElement.scrollWidth);
        expect(sw, "horizontal page scroll at 390px").toBeLessThanOrEqual(viewport.width);
      }
      expect(errors).toEqual([]);
    });
  }
}

// The overlays: open, screenshot, axe, and the keyboard behaviour they promise.
for (const vpName of ["mobile", "desktop"]) {
  for (const theme of THEMES) {
    test(`ui-kit overlays ${vpName} ${theme}`, async ({ page }, ti) => {
      await openKit(page, { viewport: VIEWPORTS[vpName], theme });

      await page.getByRole("button", { name: "Open sheet" }).click();
      const sheet = page.getByRole("dialog", { name: "Add" });
      await sheet.waitFor();
      await page.screenshot({ path: path.join(outDir(ti), `uikit-sheet-${vpName}-${theme}.png`), animations: "disabled" });
      const a1 = await new AxeBuilder({ page }).include(".vui-sheet").analyze();
      expect(a1.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id)).toEqual([]);
      await page.keyboard.press("Escape");
      await expect(sheet).toBeHidden();
      await expect(page.getByRole("button", { name: "Open sheet" })).toBeFocused();   // focus returns

      await page.keyboard.press("Control+k");
      const box = page.getByRole("combobox", { name: /Search or ask Vula/ });
      await expect(box).toBeFocused();
      await box.fill("thandi");
      await expect(page.getByRole("option", { name: /Thandi Mokoena/ })).toBeVisible();
      await expect(page.getByRole("option", { name: /Ask Vula/ })).toBeVisible();
      await page.screenshot({ path: path.join(outDir(ti), `uikit-palette-${vpName}-${theme}.png`), animations: "disabled" });
      const a2 = await new AxeBuilder({ page }).include(".vui-palette").analyze();
      expect(a2.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id)).toEqual([]);
      await page.keyboard.press("Escape");
      await expect(box).toBeHidden();
    });
  }
}

test("pill tabs move with the arrow keys", async ({ page }) => {
  await openKit(page, { viewport: VIEWPORTS.desktop, theme: "light" });
  const front = page.getByRole("tab", { name: "Front house" });
  await front.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Back house" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Back house" })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
});

test("data table: sorts by a column, and stacks into rows on a phone", async ({ page }) => {
  await openKit(page, { viewport: VIEWPORTS.desktop, theme: "light" });
  const chip = (name) => page.locator(".vui-chip", { hasText: name });
  await chip("Paid").click();                 // Overdue starts on; add the others
  await chip("Sent").click();
  await chip("Draft").click();
  const table = page.locator("table.vui-table");
  await table.getByRole("button", { name: "Amount" }).click();
  await expect(table.locator("th").filter({ hasText: "Amount" })).toHaveAttribute("aria-sort", "ascending");
  await page.setViewportSize(VIEWPORTS.mobile);
  await expect(table).toBeHidden();
  await expect(page.locator(".vui-stack .vui-row").first()).toBeVisible();
});
