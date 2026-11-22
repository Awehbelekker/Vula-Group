// accountant.spec.js — an accountant's view-only login (19 Nov 2026, Phase 3 of Xero-quality books):
// their own workspace (Overview, Reports, Bank, Invoices, Documents), switching between clients,
// documents opened through the server, and nothing that changes the books on screen. Plus the owner's
// Team › Accountant card: invite, remove, activity. Axe-clean with no sideways scroll at all three
// widths, light and dark.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS } from "./screens.js";
import { installMockApi, seedSession } from "./mockApi.js";
import * as F from "./fixtures/demo.js";

const out = (ti, name) => path.join(ti.project.testDir, ".results", "accountant", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function openWorkspace(page, viewport, theme = "light") {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await installMockApi(page, { role: "accountant" });
  await seedSession(page, { role: "accountant", theme });
  await page.goto("/");
  await page.getByText("Accountant's view").first().waitFor();
  return errors;
}

test.describe("the accountant's workspace", () => {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const [tab, theme] of [["Overview", "light"], ["Reports", "dark"], ["Bank", "light"], ["Invoices", "dark"], ["Documents", "light"]]) {
      test(`${tab} ${vp}`, async ({ page }, ti) => {
        const errors = await openWorkspace(page, viewport, theme);
        await page.getByRole("tab", { name: tab }).click();
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(250);
        await page.screenshot({ path: out(ti, `${tab.toLowerCase()}-${vp}.png`), fullPage: true, animations: "disabled" });
        expect(bad(await new AxeBuilder({ page }).include(".vac").analyze())).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
        expect(errors).toEqual([]);
      });
    }
  }

  test("only reads: no write requests, and the shell has no business menu", async ({ page }) => {
    const writes = [];
    page.on("request", (r) => { if (r.url().includes("vula.test") && !["GET", "HEAD", "OPTIONS"].includes(r.method())) writes.push(`${r.method()} ${r.url()}`); });
    await openWorkspace(page, VIEWPORTS.desktop);
    for (const tab of ["Overview", "Reports", "Bank", "Invoices", "Documents"]) {
      await page.getByRole("tab", { name: tab }).click();
      await page.waitForLoadState("networkidle");
    }
    expect(writes).toEqual([]);
    await expect(page.locator(".v2-shell")).toHaveCount(0);
    await expect(page.getByText("View only")).toBeVisible();
  });

  test("the books check and the statements to fetch are on the overview", async ({ page }) => {
    await openWorkspace(page, VIEWPORTS.desktop);
    await expect(page.getByText("Agreed by the business")).toBeVisible();
    await expect(page.getByText("Missing Jul 2026, Oct 2026")).toBeVisible();
  });

  test("the year-end pack is on the overview, for the year chosen", async ({ page }) => {
    await openWorkspace(page, VIEWPORTS.desktop);
    await expect(page.getByRole("heading", { name: "Year-end pack" })).toBeVisible();
    const pick = page.getByLabel("Year for the pack");
    const last = await pick.locator("option").nth(1).getAttribute("value");
    await pick.selectOption(last);
    const xlsx = page.waitForRequest((r) => r.url().includes("/finance-folders/year-end-pack.xlsx"));
    await page.getByRole("button", { name: "Workbook (Excel)" }).click();
    expect(new URL((await xlsx).url()).searchParams.get("year")).toBe(last);
  });

  test("a document is opened through the server, and the ledger downloads", async ({ page }) => {
    await openWorkspace(page, VIEWPORTS.desktop);
    await page.getByRole("tab", { name: "Documents" }).click();
    await page.getByRole("button", { name: /^01 Bank statements/ }).click();
    const opened = page.waitForRequest((r) => /\/v1\/documents\/demo-shop\/filed\/st-2\/open$/.test(r.url()));
    const popup = page.waitForEvent("popup").catch(() => null);
    await page.getByRole("button", { name: "FNB statement …7663, 1 Sep 2026 to 30 Sep 2026" }).click();
    await opened;
    await popup;
    await page.getByRole("tab", { name: "Reports" }).click();
    const csv = page.waitForRequest((r) => r.url().includes("/reports/ledger.csv"));
    await page.getByRole("button", { name: "General ledger (CSV)" }).click();
    await csv;
    const pdf = page.waitForRequest((r) => r.url().includes("/reports/statements.pdf"));
    await page.getByRole("button", { name: "Financial statements (PDF)" }).click();
    await pdf;
    await page.getByRole("tab", { name: "Bank" }).click();
    const lines = page.waitForRequest((r) => r.url().includes("/bank/transactions.csv"));
    await page.getByRole("button", { name: "Download bank lines (CSV)" }).click();
    await lines;
  });

  test("an accountant with several clients switches business", async ({ page }) => {
    await openWorkspace(page, VIEWPORTS.desktop);
    const next = page.waitForRequest((r) => r.url().includes("/v1/commerce/atlas-build/admin/books-audit/latest"));
    await page.getByLabel("Business").selectOption("atlas-build");
    await next;
    expect(await page.evaluate(() => localStorage.getItem("vula.accountant.tenant"))).toBe("atlas-build");
  });
});

test.describe("Team › Accountant", () => {
  const TEAM = async (page) => {
    const { openScreen } = await import("./screens.js");
    return openScreen(page, { name: "team", role: "owner", nav: "Team", ready: "Logins & passwords" },
      { viewport: VIEWPORTS.desktop, theme: "light" });
  };

  test("the owner invites, sees the activity and removes access", async ({ page }) => {
    await TEAM(page);
    const card = page.locator(".vtm-block", { hasText: "Give your accountant a view-only login" });
    await expect(card).toContainText("Lerato Ledgerline");
    await card.getByLabel("Accountant's email").fill("new@firm.example.test");
    const sent = page.waitForRequest((r) => r.url().endsWith("/v1/users/demo-shop/accountants") && r.method() === "POST");
    await card.getByRole("button", { name: "Invite the accountant" }).click();
    expect((await sent).postDataJSON().email).toBe("new@firm.example.test");
    await expect(card).toContainText(`one-time password: ${F.DEMO_ONE_TIME}`);
    await card.getByText("Accountant activity").click();
    await expect(card).toContainText("downloaded General ledger (CSV)");
    await expect(card).toContainText("FNB …7663 Sep.pdf");
    const gone = page.waitForRequest((r) => r.url().endsWith("/accountants/acc-1") && r.method() === "DELETE");
    await card.getByRole("button", { name: "Remove Lerato Ledgerline's access" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove access" }).click();
    await gone;
  });
});
