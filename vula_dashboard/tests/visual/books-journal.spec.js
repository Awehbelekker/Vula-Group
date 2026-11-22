// books-journal.spec.js — Money › Accounts for a business on the one book (18 Nov 2026, books_v2): the
// reports come from its journal, read on the accrual or the cash view; the choice goes to the P&L,
// the ledger export and the trial balance, and is remembered. A business not on it sees no toggle.
// Axe-clean with no sideways scroll at all three widths, light and dark.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, openScreen } from "./screens.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "books-journal", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
const BOOKS = { name: "books", role: "owner", nav: "Money", tab: "Accounts", ready: "Net profit", features: ["books_v2"] };

test.describe("books from the journal", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of ["light", "dark"]) {
      test(`accounts ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, BOOKS, { viewport, theme });
        await expect(page.getByText(/^From your books/)).toBeVisible();
        await page.screenshot({ path: out(ti, `accounts-${vp}-${theme}.png`), fullPage: true, animations: "disabled" });
        expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
        expect(errors).toEqual([]);
      });
    }
  }

  test("the cash view is asked for, shown, exported and remembered", async ({ page }) => {
    await openScreen(page, BOOKS, { viewport: VIEWPORTS.desktop, theme: "light" });
    const content = page.locator(".v2-content");
    await expect(content).toContainText(/R.62.996\.00/);                // accrual net profit
    const cash = page.waitForRequest((r) => r.url().includes("/reports/pnl") && r.url().includes("basis=cash"));
    await page.getByRole("tab", { name: "Cash" }).click();
    await cash;
    await expect(content).toContainText(/R.60.796\.00/);
    await expect(content).toContainText("when the money moved");
    const csv = page.waitForRequest((r) => r.url().includes("/reports/ledger.csv"));
    await page.getByRole("button", { name: "Export for accountant (CSV)" }).click();
    expect((await csv).url()).toContain("basis=cash");
    expect(await page.evaluate(() => localStorage.getItem("vula.books.basis"))).toBe("cash");
    await expect(content).toContainText("from your books (invoice basis)");
  });

  test("a business not on the one book sees no view toggle", async ({ page }) => {
    await openScreen(page, { ...BOOKS, features: [] }, { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByText("built from the reconciled bank statement")).toBeVisible();
    await expect(page.getByRole("tab", { name: "Cash" })).toHaveCount(0);
  });
});
