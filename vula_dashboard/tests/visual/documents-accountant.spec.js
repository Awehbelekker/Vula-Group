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

  test("the year-end pack downloads as a workbook, or with every document", async ({ page }) => {
    await openFolders(page, VIEWPORTS.desktop, "light");
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Year-end pack");
    const xlsx = page.waitForRequest((r) => r.url().includes("/finance-folders/year-end-pack.xlsx"));
    await page.getByRole("button", { name: "Workbook (Excel)" }).click();
    expect(new URL((await xlsx).url()).searchParams.get("year")).toMatch(/^\d{4}-03-01$/);
    const zip = page.waitForRequest((r) => r.url().includes("/finance-folders/year-end-pack.zip"));
    await page.getByRole("button", { name: "With every document (zip)" }).click();
    await zip;
    await expect(page.getByText("1 file couldn't be fetched — the index lists them.")).toBeVisible();
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

  test("company and tax documents: upload under a kind, and file the ones already on file", async ({ page }) => {
    await openFolders(page, VIEWPORTS.desktop, "light");
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Company and tax documents");
    await expect(content).toContainText("00 Company and tax");
    const own = page.getByLabel("File CRA01 - Harbour & Vine.pdf under SARS and tax");
    const other = page.getByLabel("File Bank confirmation letter.pdf under Compliance (B-BBEE, COIDA, UIF, FICA)");
    await expect(own).toBeChecked();
    await expect(other).not.toBeChecked();                       // names another business: never pre-ticked
    const filed = page.waitForRequest((r) => r.url().endsWith("/company-docs/refile"));
    await page.getByRole("button", { name: "File 1 under Company and tax" }).click();
    expect((await filed).postDataJSON()).toEqual({ ids: ["cp-1"] });
    await page.getByLabel("What you're uploading").selectOption("Company Registration");
    const up = page.waitForRequest((r) => r.url().endsWith("/ingest") && r.method() === "POST");
    await page.getByLabel("Upload company documents").setInputFiles({ name: "COR14.3.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF") });
    expect((await up).postData()).toContain("Company Registration");
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

// 20 Nov 2026 (Ian): "it would be nice if Vula can send this to a client when asked" — Send on a document.
test.describe("send a document", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of ["light", "dark"]) {
      test(`send dialog ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, DOCS(), { viewport, theme });
        await page.getByRole("button", { name: "Send Health-certificate-2026.pdf" }).click();
        const dialog = page.getByRole("dialog", { name: "Send this document" });
        await dialog.getByLabel(/^To/).fill("Atlantis Foods");
        await dialog.getByRole("button", { name: "Preview" }).click();
        await expect(dialog.getByRole("status")).toContainText("to Atlantis Foods (accounts@example.com) by email");
        await page.screenshot({ path: out(ti, `send-${vp}-${theme}.png`), fullPage: true, animations: "disabled" });
        expect(bad(await new AxeBuilder({ page }).include('[role="dialog"]').analyze())).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
        expect(errors).toEqual([]);
      });
    }
  }

  test("preview sends nothing; Send sends the confirmed request", async ({ page }) => {
    await openScreen(page, DOCS(), { viewport: VIEWPORTS.desktop, theme: "light" });
    const bodies = [];
    page.on("request", (r) => { if (/\/filed\/[^/]+\/send$/.test(r.url())) bodies.push(r.postDataJSON()); });
    await page.getByRole("button", { name: "Send Health-certificate-2026.pdf" }).click();
    const dialog = page.getByRole("dialog", { name: "Send this document" });
    await dialog.getByLabel(/^To/).fill("accounts@atlantis.co.za");
    await dialog.getByLabel(/^Send by/).selectOption("email");
    await dialog.getByRole("button", { name: "Preview" }).click();
    await expect(dialog.getByRole("status")).toBeVisible();
    expect(bodies).toEqual([{ to: "accounts@atlantis.co.za", channel: "email", note: null, confirm: false }]);
    await dialog.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText(/^Sent our bank confirmation letter/)).toBeVisible();
    expect(bodies[1]).toEqual({ to: "accounts@atlantis.co.za", channel: "email", note: null, confirm: true });
    await expect(dialog).toBeHidden();
  });
});
