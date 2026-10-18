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

const CUSTOMERS = { name: "customers", role: "owner", nav: "People", tab: "Customers", ready: "customers in this segment" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`customers ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, CUSTOMERS, { viewport, theme });
      await check(page, ti, "customers", `${vp}-${theme}`, viewport);
      await page.getByRole(vp === "mobile" ? "button" : "row", { name: /Thandi Mokoena/ }).first().click();
      const sheet = page.getByRole("dialog", { name: "Thandi Mokoena" });
      await expect(sheet.getByText("Prefers delivery before 10am. Wholesale account.")).toBeVisible();
      await page.screenshot({ path: out(ti, `customers-open-${vp}-${theme}.png`), animations: "disabled" });
      expect(bad(await new AxeBuilder({ page }).include(".vui-sheet").analyze())).toEqual([]);
      expect(errors).toEqual([]);
    });
  }
}

test("customers: segments narrow the list; money in rand from cents", async ({ page }) => {
  await openScreen(page, CUSTOMERS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.locator(".vui-table tbody tr")).toHaveCount(10);
  await page.getByRole("button", { name: /High value/ }).click();
  await expect(page.locator(".vui-table tbody tr")).toHaveCount(6);
  await expect(page.getByText(/6 customers in this segment/)).toBeVisible();
  await expect(page.locator(".vui-table tbody tr").first()).toContainText("R 4 128.50");
});

const ORDERS = { name: "orders", role: "owner", nav: "Sell", tab: "Orders", ready: "Paid, packed and sent" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`orders ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, ORDERS, { viewport, theme });
      await expect(page.getByText("HV-1052", { exact: true }).locator("visible=true").first()).toBeVisible();
      await check(page, ti, "orders", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("orders: status chips filter on the server; phone cards keep the next-status buttons", async ({ page }) => {
  await openScreen(page, ORDERS, { viewport: VIEWPORTS.mobile, theme: "light" });
  const cards = page.locator(".vor-card");
  const all = await cards.count();
  expect(all).toBeGreaterThan(2);
  await page.getByRole("button", { name: "Unpaid" }).click();
  await expect(cards.first()).toContainText("Awaiting payment");
  expect(await cards.count()).toBeLessThan(all);
  await expect(cards.first().getByRole("button", { name: "Delete" })).toBeVisible();
  await page.getByRole("button", { name: "Unpaid" }).click();
  await page.getByRole("button", { name: "Paid", exact: true }).click();
  await expect(cards.first().getByRole("button", { name: "Confirmed" })).toBeVisible();
});

const INVOICES = { name: "invoices", role: "owner", nav: "Money", tab: "Invoices", ready: "Invoices, quotes and credit notes you send." };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`invoices ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, INVOICES, { viewport, theme });
      await expect(page.getByText("INV-2026-0118", { exact: true }).locator("visible=true").first()).toBeVisible();
      await check(page, ti, "invoices", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: "More for INV-2026-0118" }).locator("visible=true").first().click();
      await expect(page.getByRole("dialog", { name: "More for INV-2026-0118" })).toBeVisible();
      expect(bad(await new AxeBuilder({ page }).include(".vui-sheet").analyze())).toEqual([]);
      await page.screenshot({ path: out(ti, `invoices-more-${vp}-${theme}.png`), animations: "disabled" });
      expect(errors).toEqual([]);
    });
  }
}

test("invoices: direction and type switch the list; quotes get the bulk delete", async ({ page }) => {
  await openScreen(page, INVOICES, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("tab", { name: "From suppliers" }).click();
  await expect(page.getByText("Bills your suppliers sent you")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Credit notes" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Sent to clients" }).click();
  await page.getByRole("tab", { name: "Quotes" }).click();
  const pick = page.getByRole("checkbox").locator("visible=true").first();
  await pick.check();
  await expect(page.getByRole("region", { name: "Selected quotes" })).toContainText("1 selected");
});

test("Stock and Suppliers live under Operate; a link to the old place still opens Stock", async ({ page }) => {
  await openScreen(page, { name: "home", role: "owner", nav: "Sell", ready: "Paid, packed and sent" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.locator('[role=tab][data-tab="stock"]')).toHaveCount(0);
  await expect(page.locator('[role=tab][data-tab="suppliers"]')).toHaveCount(0);
  await page.locator(".v2-nav-item", { has: page.locator(".v2-nav-lb", { hasText: /^Operate$/ }) }).click();
  await expect(page.locator('[role=tab][data-tab="stock"]')).toBeVisible();
  await expect(page.locator('[role=tab][data-tab="suppliers"]')).toBeVisible();
  // Home's Low stock tile links { section: "sell", tab: "stock" }
  await page.locator(".v2-nav-item", { has: page.locator(".v2-nav-lb", { hasText: /^Home$/ }) }).click();
  await page.getByRole("tab", { name: "Back house" }).click();
  await page.getByRole("button", { name: /Low stock/ }).click();
  await expect(page.locator('[role=tab][data-tab="stock"][aria-selected="true"]')).toBeVisible();
  await expect(page.locator(".v2-title")).toHaveText("Operate");
});

const EXPENSES = { name: "expenses", role: "owner", nav: "Money", tab: "Expenses", ready: "Receipts sent on WhatsApp appear here" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`expenses ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, EXPENSES, { viewport, theme });
      await expect(page.getByText("Sourdough flour, 25kg").locator("visible=true").first()).toBeVisible();
      await check(page, ti, "expenses", `${vp}-${theme}`, viewport);
      await page.getByText("Platter boards (x12)").locator("visible=true").first().click();
      const sheet = page.getByRole("dialog", { name: "Platter boards (x12)" });
      await expect(sheet.getByLabel("Project")).toBeVisible();
      expect(bad(await new AxeBuilder({ page }).include(".vui-sheet").analyze())).toEqual([]);
      await page.screenshot({ path: out(ti, `expenses-open-${vp}-${theme}.png`), animations: "disabled" });
      expect(errors).toEqual([]);
    });
  }
}

test("expenses: what needs a person is flagged; allocating posts the same assign call", async ({ page }) => {
  await openScreen(page, EXPENSES, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText("Needs project + purpose").locator("visible=true")).toBeVisible();
  await expect(page.getByText("Not booked yet").locator("visible=true")).toBeVisible();          // the 'unconfirmed' status (migration 216)
  await expect(page.getByText("Owed to people").locator("..")).toContainText("R 850");
  const posted = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/expenses\/exp-007\/assign$/.test(new URL(r.url()).pathname));
  await page.getByText("Platter boards (x12)").locator("visible=true").click();
  await page.getByRole("dialog").getByLabel("Project").selectOption("Bay Events platters");
  expect((await posted).postDataJSON()).toEqual({ project: "Bay Events platters" });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Reimbursable" }).click();
  await expect(page.locator(".vex-table tbody tr")).toHaveCount(2);
});

const CONTACTS = { name: "contacts", role: "owner", nav: "People", tab: "Contacts", ready: "Your email directory" };
const FOLLOWUPS = { name: "followups", role: "owner", modules: ["followups"], nav: "People", tab: "Follow-ups", ready: "Emails Vula thinks are waiting on you" };

for (const s of [CONTACTS, FOLLOWUPS]) {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`${s.name} ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, s, { viewport, theme });
        await check(page, ti, s.name, `${vp}-${theme}`, viewport);
        expect(errors).toEqual([]);
      });
    }
  }
}

test("contacts: kind chips and search filter; re-tag posts the same PATCH", async ({ page }) => {
  await openScreen(page, CONTACTS, { viewport: VIEWPORTS.desktop, theme: "light" });
  const rows = page.locator(".vui-table tbody tr");
  await expect(rows).toHaveCount(5);
  await page.getByRole("button", { name: /Suppliers/ }).click();
  await expect(rows).toHaveCount(2);
  await page.getByRole("searchbox").fill("eureka");
  await expect(rows).toHaveCount(1);
  const patched = page.waitForRequest((r) => r.method() === "PATCH" && /\/v1\/email\/contacts\/[^/]+\/ec-3$/.test(new URL(r.url()).pathname));
  await rows.first().getByLabel("Kind for Eureka Mills").selectOption("client");
  expect((await patched).postDataJSON()).toEqual({ kind: "client" });
});

test("follow-ups: Done posts status done", async ({ page }) => {
  await openScreen(page, FOLLOWUPS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await expect(page.getByText("3d waiting")).toBeVisible();
  const patched = page.waitForRequest((r) => r.method() === "PATCH" && /\/v1\/email\/followups\/[^/]+\/fu-1$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Done" }).first().click();
  expect((await patched).postDataJSON()).toEqual({ status: "done" });
});
