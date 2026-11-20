// books-review.spec.js — Money › Books review (18 Nov 2026). The business works through what Vula's
// books check found: set-aside lines put back (with a reason), copies set aside, transfers, payments in
// transit tied to their bank line, staff claims paid back, drafts and dates, official statements
// compared and the ticked actions applied — and agrees the check (with a note while items are open).
// Owners and managers only. Axe-clean with no sideways scroll at all three widths, light and dark.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, openScreen } from "./screens.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "books-review", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

const SCREEN = (role = "owner") => ({ name: "booksreview", role, nav: "Money", tab: "Books review",
  ready: "Work through what Vula found in your books" });

async function check(page, ti, name, viewport) {
  await page.screenshot({ path: out(ti, `${name}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

async function answer(page, text) {
  const box = page.getByRole("dialog").locator("textarea");
  await box.fill(text);
  await page.getByRole("dialog").getByRole("button", { name: /OK|Save|Continue|Done/ }).click();
}

test.describe("books review", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const [tab, theme] of [["Overview", "light"], ["Bank lines", "dark"], ["In transit", "light"],
      ["Paperwork", "dark"], ["Documents", "dark"], ["Statements", "light"]]) {
      test(`${tab} ${vp}`, async ({ page }, ti) => {
        const errors = await openScreen(page, SCREEN(), { viewport, theme });
        await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
        await page.waitForTimeout(200);
        await check(page, ti, `${tab.replace(/ /g, "-").toLowerCase()}-${vp}`, viewport);
        expect(errors).toEqual([]);
      });
    }
  }

  test("staff don't see the tab", async ({ page }) => {
    await openScreen(page, { name: "home", role: "staff", nav: null, ready: "Chats waiting on you" },
      { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByText("Books review")).toHaveCount(0);
  });

  test("the overview shows each check and the books as they would be", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    const content = page.locator(".v2-content");
    await expect(content).toContainText("2 real lines were set aside");
    await expect(content).toContainText("You owe suppliers: R 0.00");
    await expect(content).toContainText("In transit (paid, with no bank line): R 23 000.00");
    await expect(content).toContainText("Owed to staff for claims: R 4 000.00");
  });

  test("lines are put back with a reason, copies set aside, a transfer confirmed", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /^Bank lines/ }).click();
    const restored = page.waitForRequest((r) => r.url().endsWith("/books-audit/lines/restore"));
    await page.getByRole("button", { name: "Put the chosen lines back" }).click();
    await answer(page, "The running balance shows these are real.");
    const body = (await restored).postDataJSON();
    expect(body).toEqual({ ids: ["r1", "r2"], reason: "The running balance shows these are real." });
    const aside = page.waitForRequest((r) => r.url().endsWith("/books-audit/lines/set-aside"));
    await page.getByRole("button", { name: "Keep the first, set the rest aside" }).click();
    expect((await aside).postDataJSON().ids).toEqual(["d2"]);
    const tr = page.waitForRequest((r) => r.url().endsWith("/books-audit/lines/transfer"));
    await page.getByRole("button", { name: "Yes, it's a transfer" }).click();
    expect((await tr).postDataJSON().ids).toEqual(["t1", "t2"]);
  });

  test("lines an old rule filed are re-filed once per payee", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByRole("tab", { name: /^Bank lines/ })).toContainText("(17)");
    await page.getByRole("tab", { name: /^Bank lines/ }).click();
    const content = page.locator(".v2-content");
    await expect(content).toContainText("FNB App Payment To Skipp Rubble Removal · 8 lines");
    await expect(content).toContainText("these are bank charges");
    const sent = page.waitForRequest((r) => r.url().endsWith("/bank/transactions/bulk-categorize"));
    await page.getByLabel("Account for FNB App Payment To Skipp Rubble Removal").selectOption("cost_of_sales");
    await page.getByRole("dialog").getByRole("button", { name: "File as Cost of sales" }).click();
    expect((await sent).postDataJSON()).toEqual({ txn_ids: ["sk1", "sk2", "sk3", "sk4", "sk5", "sk6", "sk7", "sk8"],
      account_code: "cost_of_sales" });
    await expect(content).not.toContainText("Skipp Rubble Removal · 8 lines");
  });

  test("a bank payment pays the supplier's open bill, under the account chosen", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /^Bank lines/ }).click();
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Payments that pay a supplier's bill");
    const paid = page.getByRole("button", { name: "Paid DIG-BILL-00196 (2026-07-13, HPC Bokaap)" });
    await paid.click();                                  // no account chosen yet: nothing is sent
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByLabel("Account for payments to Belekkie Skips (R Engelbrecht)").selectOption("cost_of_sales");
    const sent = page.waitForRequest((r) => r.url().endsWith("/books-audit/lines/p1/pay-bill"));
    await paid.click();
    await page.getByRole("dialog").getByRole("button", { name: "It paid DIG-BILL-00196" }).click();
    expect((await sent).postDataJSON()).toEqual({ bill_ids: ["bill-196"], account_code: "cost_of_sales" });
    await expect(content).not.toContainText("Paid DIG-BILL-00196");
    // one transfer for two bills: the open one paid, the one already marked paid only tied
    const two = page.getByRole("button", { name: /Paid DIG-BILL-00081 R\s?5\s?600\.00 \+ DIG-BILL-00197 R\s?2\s?800\.00 \(marked paid\)/ });
    const both = page.waitForRequest((r) => r.url().endsWith("/books-audit/lines/p8/pay-bill"));
    await two.click();
    await expect(page.getByRole("dialog")).toContainText("DIG-BILL-00197 is already marked paid");
    await page.getByRole("dialog").getByRole("button", { name: "It paid DIG-BILL-00081 + DIG-BILL-00197" }).click();
    expect((await both).postDataJSON()).toEqual({ bill_ids: ["bill-81", "bill-97"], account_code: "cost_of_sales" });
    await expect(content).not.toContainText("Payments that pay a supplier's bill");
  });

  test("a payment in transit is tied to its bank line", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /^In transit/ }).click();
    const linked = page.waitForRequest((r) => r.url().endsWith("/books-audit/lines/p1/link"));
    await page.getByRole("button", { name: "This is the payment" }).click();
    expect((await linked).postDataJSON()).toEqual({ invoice_id: "bill-35" });
    await expect(page.locator(".v2-content")).toContainText("Every recorded payment has its bank line.");
  });

  test("a statement is uploaded as a file and only the ticked actions are applied", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /^Statements/ }).click();
    const up = page.waitForRequest((r) => r.url().endsWith("/books-audit/statements") && r.method() === "POST");
    await page.getByLabel("Statement PDFs").setInputFiles({ name: "FNB Sep.pdf", mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 demo") });
    await page.getByRole("button", { name: "Compare it" }).click();
    const req = await up;
    expect(req.headers()["content-type"]).toContain("multipart/form-data");
    await page.getByText("40 lines already right").waitFor();
    const applied = page.waitForRequest((r) => r.url().endsWith("/statements/chk-1/apply"));
    await page.getByRole("button", { name: "Apply the ticked ones" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Apply" }).click();
    expect((await applied).postDataJSON().accept).toEqual(["a1", "a2"]);     // "check by hand" is never ticked
  });

  test("agreeing while items are open needs a note", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("button", { name: "Agree, with what's still open noted" }).click();
    await expect(page.getByText("Some items are still open — say in the note")).toBeVisible();   // the toast
    await page.getByLabel("Note for your accountant").fill("The draft from PUDLO is being checked with the supplier.");
    const agreed = page.waitForRequest((r) => r.url().endsWith("/books-audit/audit-1/agree"));
    await page.getByRole("button", { name: "Agree, with what's still open noted" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Agree" }).click();
    expect((await agreed).postDataJSON().note).toContain("PUDLO");
    await expect(page.locator(".v2-content")).toContainText("Vula's team signs it off next");
  });

  // ── Documents (19 Nov 2026, vula/commerce/doc_audit.py) ──
  async function docs(page) {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /^Documents/ }).click();
  }

  test("the documents tab counts what's open and lists each finding", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await expect(page.getByRole("tab", { name: "Documents (15)" })).toBeVisible();
    await page.getByRole("tab", { name: /^Documents/ }).click();
    const content = page.locator(".v2-content");
    await expect(content).toContainText("The total read as R2,500.00 isn't in the document itself.");
    await expect(content).toContainText("STE Scaffolding STE00866");
    await expect(content).toContainText("Booked at R2,500.00, the document says R2,300.00.");
    await expect(content).toContainText("Cancel this copy");
    await expect(content).toContainText("Everything — no sections chosen");
  });

  test("correcting figures sends cents as typed, with the reason", async ({ page }) => {
    await docs(page);
    await page.getByRole("button", { name: "Correct the figures" }).first().click();
    const form = page.getByRole("group", { name: /Correct the figures on Invoice from Build It/ });
    await form.getByLabel("Total (R)", { exact: true }).fill("2 300.00");
    const sent = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/doc-bb55/figures"));
    await form.getByRole("button", { name: "Save the corrected figures" }).click();
    await expect(page.getByText("Say why")).toBeVisible();                       // a reason is required
    await form.getByLabel("Why (kept for the accountant)").fill("The PDF says R2 300.00");
    await form.getByRole("button", { name: "Save the corrected figures" }).click();
    expect((await sent).postDataJSON()).toEqual({ changes: { total_cents: 230000 }, reason: "The PDF says R2 300.00" });
  });

  test("a document is opened through the server", async ({ page }) => {
    await docs(page);
    const opened = page.waitForRequest((r) => r.url().includes("/filed/doc-ste/open"));
    const popup = page.waitForEvent("popup").catch(() => null);
    await page.getByRole("button", { name: /Open Invoice from STE Scaffolding/ }).click();
    await opened;
    await popup;
  });

  test("an invoice on file is booked, a bill matched to its document, a copy cancelled", async ({ page }) => {
    await docs(page);
    const booked = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/doc-ste/book"));
    await page.getByRole("button", { name: "Book it", exact: true }).first().click();
    await page.getByRole("dialog").getByRole("button", { name: "Book it" }).click();
    await booked;
    const matched = page.waitForRequest((r) => r.url().endsWith("/books-audit/bills/bill-bb55/match-document"));
    await page.getByRole("button", { name: "Book it at the document's figures" }).click();
    await answer(page, "The bill was booked at a misread total.");
    expect((await matched).postDataJSON().reason).toBe("The bill was booked at a misread total.");
    const cancelled = page.waitForRequest((r) => r.url().endsWith("/books-audit/bills/bill-hpc2/cancel-duplicate"));
    await page.getByRole("button", { name: "Cancel this copy" }).click();
    await answer(page, "The same invoice booked twice.");
    expect((await cancelled).postDataJSON()).toEqual({ of_id: "bill-hpc1", reason: "The same invoice booked twice." });
  });

  test("copies, suppliers and a document's VAT basis", async ({ page }) => {
    await docs(page);
    const dup = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/doc-q2/duplicate"));
    await page.getByRole("button", { name: "Keep the first, the rest are copies" }).click();
    expect((await dup).postDataJSON()).toEqual({ of_id: "doc-q1" });
    const merged = page.waitForRequest((r) => r.url().endsWith("/suppliers/merge"));
    await page.getByRole("button", { name: "Merge into one" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Merge" }).click();
    expect((await merged).postDataJSON()).toEqual({ keep_id: "sup-1", duplicate_ids: ["sup-2"] });
    const basis = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/doc-pudlo/vat-basis"));
    await page.getByLabel(/VAT on the prices in Quote/).selectOption("excl");
    expect((await basis).postDataJSON()).toEqual({ basis: "excl" });
  });

  test("the business's own invoices are never offered for booking; one addressed elsewhere says so", async ({ page }) => {
    await docs(page);
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Your own invoices on file");
    await expect(content).toContainText("Addressed to Enziglo (Pty) Ltd");
    const own = page.locator("li", { hasText: "DIGG-2026-HPC02" });
    await expect(own.getByRole("button", { name: "Book it" })).toHaveCount(0);
    const noted = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/doc-own/not-a-bill"));
    await own.getByRole("button", { name: "It's a sale" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Yes, it's a sale" }).click();
    expect((await noted).postDataJSON().reason).toContain("Our own invoice to Vizion Developments");
    await expect(content).not.toContainText("Your own invoices on file");
  });

  test("payment certificates are filed as certificates and own invoices are added to Invoices, paid", async ({ page }) => {
    await docs(page);
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Payment certificates");
    await expect(content).toContainText("Your invoice DIGG-2026-HPC02 (on file, not in Invoices)");
    await expect(content).toContainText("Certificate 5 · R 387 297.60 received 2026-07-24");
    const filed = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/pc2/certificate"));
    await page.getByRole("button", { name: "File as a certificate" }).click();
    await filed;
    await expect(page.getByRole("button", { name: "File as a certificate" })).toHaveCount(0);
    await expect(content).toContainText("Your invoices not in Invoices");
    await expect(content).toContainText("15% VAT added");
    const hpc14 = page.locator("li", { hasText: "15% VAT added" });
    await expect(hpc14.getByRole("button", { name: "Add to Invoices" })).toHaveCount(0);   // fix it in Invoices first
    const added = page.waitForRequest((r) => r.url().endsWith("/books-audit/docs/doc-hpc02/add-to-invoices"));
    await page.getByRole("button", { name: "Add to Invoices" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Add to Invoices" }).click();
    expect((await added).postDataJSON()).toEqual({ txn_id: "r2" });
  });

  test("a supplier's statements are checked against the books and the missing invoices can be asked for", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await docs(page);
    const content = page.locator(".v2-content");
    await expect(content).toContainText("Suppliers' statements");
    await expect(content).toContainText("owed on the statements R 36 832.07, in your books R 0.00");
    await expect(content).toContainText("2 invoices on the statement aren't in Vula");
    await expect(content).toContainText("no statement on file for those months from FNB …4607");
    await page.getByRole("button", { name: "Copy a request for the missing invoices" }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("INV-0653, INV-1615");
  });

  test("several statements are compared in one go, each on its own", async ({ page }) => {
    await openScreen(page, SCREEN(), { viewport: VIEWPORTS.desktop, theme: "light" });
    await page.getByRole("tab", { name: /^Statements/ }).click();
    const posts = [];
    page.on("request", (r) => { if (r.url().endsWith("/books-audit/statements") && r.method() === "POST") posts.push(r); });
    await page.getByLabel("Statement PDFs").setInputFiles([
      { name: "FNB …7663 Jul.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 a") },
      { name: "FNB …7663 Aug.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 b") },
      { name: "FNB …4607 Mar.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 c") }]);
    await page.getByRole("button", { name: "Compare 3 statements" }).click();
    await expect(page.getByRole("list", { name: "Statements just compared" }).getByRole("listitem")).toHaveCount(3);
    expect(posts.length).toBe(3);
    expect(posts.every((r) => r.headers()["content-type"].includes("multipart/form-data"))).toBe(true);
    await page.getByRole("button", { name: "Open the comparison for FNB …7663 Aug.pdf" }).click();
    await page.getByText("40 lines already right").waitFor();
  });
});
