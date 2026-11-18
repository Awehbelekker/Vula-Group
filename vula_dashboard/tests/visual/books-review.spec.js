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
      ["Paperwork", "dark"], ["Statements", "light"]]) {
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
    await page.getByLabel("Statement PDF").setInputFiles({ name: "FNB Sep.pdf", mimeType: "application/pdf",
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
});
