// management.spec.js — Money › Management accounts and Home's "Financial position" (23 Nov 2026).
// The month against last month and the year to date, cash per account, what's owed, key figures and
// notes, all as the server sent them; a month picker; PDF and Excel downloads for the month shown.
// The accountant downloads the same pack from Reports. Axe-clean with no sideways scroll at all three
// widths, light and dark. Made-up business.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, openScreen } from "./screens.js";
import { installMockApi, seedSession } from "./mockApi.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "management", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

const SCREEN = { name: "management", role: "owner", nav: "Money", tab: "Management accounts",
  ready: "How the business did in the month" };

async function check(page, ti, name, viewport) {
  await page.screenshot({ path: out(ti, `${name}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

test.describe("management accounts", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of ["light", "dark"]) {
      test(`the month ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, SCREEN, { viewport, theme });
        await page.getByText("Sales R2,550.00 — up 27.5% on September.").waitFor();
        await check(page, ti, `month-${vp}-${theme}`, viewport);
        expect(errors).toEqual([]);
      });
    }
  }

  test("the figures, the columns and the notes are the server's", async ({ page }) => {
    await openScreen(page, SCREEN, { viewport: VIEWPORTS.desktop, theme: "light" });
    const content = page.locator(".v2-content");
    await expect(content).toContainText("October 2026");
    await expect(content).toContainText("From the bank statements: money in and out, by category.");
    for (const col of ["October 2026", "September 2026", "Change", "Year to date"]) {
      await expect(page.getByRole("columnheader", { name: col })).toBeVisible();
    }
    await expect(page.getByRole("columnheader", { name: /last year/i })).toHaveCount(0);
    await expect(content).toContainText("Casual labour · direct cost");
    await expect(content).toContainText("52.9%");
    await expect(content).toContainText("No statement on file for Cheque …5678 in October");
    await expect(content).toContainText("Atlantis Foods");
  });

  test("another month is asked for, and the first month has no comparison", async ({ page }) => {
    await openScreen(page, SCREEN, { viewport: VIEWPORTS.desktop, theme: "light" });
    const asked = page.waitForRequest((r) => r.url().includes("/reports/management?month=2026-08"));
    await page.getByLabel("Month").selectOption("2026-08");
    await asked;
    await expect(page.getByRole("columnheader", { name: "Change" })).toHaveCount(0);
  });

  test("downloads send the month shown", async ({ page }) => {
    await openScreen(page, SCREEN, { viewport: VIEWPORTS.desktop, theme: "light" });
    for (const [button, kind] of [["Download PDF", "pdf"], ["Download Excel", "xlsx"]]) {
      const asked = page.waitForRequest((r) => r.url().includes(`/reports/management.${kind}?month=2026-10`));
      const saved = page.waitForEvent("download");
      await page.getByRole("button", { name: button }).click();
      await asked;
      expect((await saved).suggestedFilename()).toBe(`Management_accounts_2026-10.${kind}`);
    }
  });

  test("Home's back house shows the financial position, which opens the tab", async ({ page }, ti) => {
    const errors = await openScreen(page, { name: "home", role: "owner", nav: null, ready: "Won in" },
      { viewport: VIEWPORTS.mobile, theme: "dark" });
    await page.getByRole("tab", { name: "Back house" }).click();
    const card = page.getByRole("region", { name: "Financial position" });
    await expect(card).toContainText("R 3 300");
    await expect(card).toContainText("Profit so far in November");
    await check(page, ti, "home-position-mobile-dark", VIEWPORTS.mobile);
    await card.getByText("In the bank").click();
    await page.getByText("How the business did in the month").waitFor();
    expect(errors).toEqual([]);
  });

  test("staff without Money see neither", async ({ page }) => {
    await openScreen(page, { name: "home", role: "staff", nav: null, ready: "Chats waiting on you" },
      { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByText("Management accounts")).toHaveCount(0);
    await expect(page.getByText("Financial position")).toHaveCount(0);
  });

  test("the accountant downloads the pack from Reports", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.tablet);
    await installMockApi(page, { role: "accountant" });
    await seedSession(page, { role: "accountant", theme: "light" });
    await page.goto("/");
    await page.getByText("Accountant's view").first().waitFor();
    await page.getByRole("tab", { name: "Reports" }).click();
    const month = page.getByLabel("Month for the management accounts");
    const value = await month.inputValue();
    const asked = page.waitForRequest((r) => r.url().includes(`/reports/management.pdf?month=${value}`));
    await page.getByRole("button", { name: "Download PDF" }).click();
    await asked;
    expect(bad(await new AxeBuilder({ page }).include(".vac").analyze())).toEqual([]);
  });
});
