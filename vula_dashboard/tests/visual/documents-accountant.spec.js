// documents-accountant.spec.js — Documents › For your accountant (19 Nov 2026): the year's money
// documents in an accountant's folders, bank statements per account with the months still missing,
// a folder or the whole year downloaded as a zip; and naming a bank account (Money › Bank & matching ›
// Tools). Owners and managers only. Axe-clean with no sideways scroll at all three widths, light and dark.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, openScreen } from "./screens.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "documents-accountant", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
const DOCS = () => ({ name: "documents", role: "owner", nav: "Operate", tab: "Documents",
  ready: "Atlantic-Dairy-invoice-BILL-0031.pdf" });

async function openFolders(page, viewport, theme) {
  const errors = await openScreen(page, DOCS(), { viewport, theme });
  await page.getByRole("tab", { name: "For your accountant" }).click();
  return errors;
}

test.describe("for your accountant", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of ["light", "dark"]) {
      test(`folders ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openFolders(page, viewport, theme);
        await expect(page.getByText("Bank statements on file")).toBeVisible();
        await page.getByRole("button", { name: /^01 Bank statements/ }).click();
        await page.screenshot({ path: out(ti, `folders-${vp}-${theme}.png`), fullPage: true, animations: "disabled" });
        expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
        expect(errors).toEqual([]);
      });
    }
  }

  test("statements are shown per account, with the months to fetch", async ({ page }) => {
    await openFolders(page, VIEWPORTS.desktop, "light");
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Business cheque …7663");
    await expect(content).toContainText("Missing: Jul 2026, Oct 2026");
    await expect(content).toContainText("Every month on file");
    await expect(content).toContainText("12 bank lines don't say which account");
    await page.getByRole("button", { name: /^01 Bank statements/ }).click();
    await expect(content).toContainText("FNB statement …7663, 1 Sep 2026 to 30 Sep 2026");
    await expect(content.getByText("Official", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /^03 Supplier invoices/ }).click();
    await expect(content).toContainText("Atlantic Dairy Co-op · 2");
  });

  test("a folder and the whole year download as a zip", async ({ page }) => {
    await openFolders(page, VIEWPORTS.desktop, "light");
    const one = page.waitForRequest((r) => r.url().includes("/finance-folders.zip"));
    await page.getByRole("button", { name: "Download 01 Bank statements" }).click();
    const u = new URL((await one).url());
    expect(u.searchParams.get("folder")).toBe("bank");
    expect(u.searchParams.get("year")).toMatch(/^\d{4}-03-01$/);
    const all = page.waitForRequest((r) => r.url().includes("/finance-folders.zip"));
    await page.getByRole("button", { name: "Download the whole year" }).click();
    expect(new URL((await all).url()).searchParams.get("folder")).toBeNull();
  });

  test("a login that isn't an owner or manager is told the folders are theirs", async ({ page }) => {
    await openScreen(page, DOCS(), { viewport: VIEWPORTS.desktop, theme: "light" });
    // the server refuses anyone else (vula/api/books_audit.py _full); a later route wins over the mocks
    await page.route(/\/admin\/finance-folders(\?|$)/, (route) => route.fulfill({ status: 403,
      contentType: "application/json", body: '{"detail":"Only an owner or manager can audit the books."}',
      headers: { "access-control-allow-origin": "*" } }));
    await page.getByRole("tab", { name: "For your accountant" }).click();
    await expect(page.getByText("Only an owner or manager can see these folders")).toBeVisible();
  });

  test("a bank account is named", async ({ page }) => {
    await openScreen(page, { name: "bank", role: "owner", nav: "Money", tab: "Bank & matching",
      ready: "ATLANTIC DAIRY COOP DEBIT ORDER" }, { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("button", { name: "Tools" }).click();
    const box = page.getByLabel("Name for account …4607");
    await box.fill("Savings");
    const saved = page.waitForRequest((r) => r.url().endsWith("/bank/accounts/ba-2") && r.method() === "POST");
    await box.blur();
    expect((await saved).postDataJSON()).toEqual({ project: null, name: "Savings" });
  });
});
