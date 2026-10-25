// migrate.spec.js — screens moved onto the redesign's components (UX redesign Phase F). UI_V2=1.
// Each screen GATES on axe (serious/critical = fail) and on no sideways scroll at 390px; the
// flag-off look of the same screens is baseline.spec.js's job.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";
import * as F from "./fixtures/demo.js";

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
  await page.getByRole("button", { name: /A person is handling/ }).click();
  await expect(rows).toHaveCount(1);
  await page.getByRole("button", { name: /A person is handling/ }).click();
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

const TEAM = { name: "team", role: "owner", nav: "Team", ready: "Logins & passwords" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`team ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, TEAM, { viewport, theme });
      await expect(page.getByText("Zanele").locator("visible=true").first()).toBeVisible();
      await check(page, ti, "team", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("team: access chips toggle with the same PATCH; Stock sits under Operate", async ({ page }) => {
  await openScreen(page, TEAM, { viewport: VIEWPORTS.desktop, theme: "light" });
  const zanele = page.locator(".vtm-member", { hasText: "Zanele" });
  const operate = zanele.getByRole("group", { name: "Operate access for Zanele" });
  await expect(operate.getByRole("button", { name: "Stock" })).toHaveAttribute("aria-pressed", "true");
  await expect(zanele.getByRole("group", { name: "Sell access for Zanele" }).getByRole("button", { name: "Stock" })).toHaveCount(0);
  const patched = page.waitForRequest((r) => r.method() === "PATCH" && /\/v1\/team\/[^/]+\/tm-2$/.test(new URL(r.url()).pathname));
  await zanele.getByRole("group", { name: "Sell access for Zanele" }).getByRole("button", { name: "Products" }).click();
  expect((await patched).postDataJSON()).toEqual({ access: ["orders", "inbox", "stock", "products"] });
  // owner: full access, every chip pressed
  const owner = page.locator(".vtm-member", { hasText: "Demo Owner" });
  await expect(owner.getByText("Full access (owner / manager)")).toBeVisible();
});

const PRODUCTS = { name: "products", role: "owner", nav: "Sell", tab: "Products", ready: "Everything you sell, by category" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`products ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, PRODUCTS, { viewport, theme });
      await expect(page.getByText("Sourdough loaf", { exact: true })).toBeVisible();
      await check(page, ti, "products", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: "Edit Smoked snoek pâté (200g)" }).click();
      await expect(page.getByRole("dialog", { name: "Smoked snoek pâté (200g)" })).toBeVisible();
      await page.screenshot({ path: out(ti, `products-edit-${vp}-${theme}.png`), animations: "disabled" });
      expect(errors).toEqual([]);
    });
  }
}

test("products: price saves in integer cents; the stock switch and star send the same PATCH", async ({ page }) => {
  await openScreen(page, PRODUCTS, { viewport: VIEWPORTS.desktop, theme: "light" });
  const price = page.getByLabel("Price for Sourdough loaf, in rand");
  const priced = page.waitForRequest((r) => r.method() === "PATCH" && /\/admin\/products\/p-02$/.test(new URL(r.url()).pathname));
  await price.fill("69.90");
  await price.press("Enter");
  expect((await priced).postDataJSON()).toEqual({ price_cents: 6990 });
  const stocked = page.waitForRequest((r) => r.method() === "PATCH" && /\/admin\/products\/p-02$/.test(new URL(r.url()).pathname));
  await page.getByRole("switch", { name: "Sourdough loaf in stock" }).click();
  expect((await stocked).postDataJSON()).toEqual({ in_stock: false });
  const starred = page.waitForRequest((r) => r.method() === "PATCH" && /\/admin\/products\/p-02$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Mark Sourdough loaf catch of the day" }).click();
  expect((await starred).postDataJSON()).toEqual({ is_daily_catch: true });
  await page.getByRole("button", { name: "Fresh fish" }).click();
  await expect(page.locator(".vpr-row")).toHaveCount(1);
});

const BOOKINGS = { name: "bookings", role: "owner", modules: ["bookings"], nav: "Sell", tab: "Bookings", ready: "Appointments, the services people can book" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`bookings ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, BOOKINGS, { viewport, theme });
      await expect(page.getByText("Fatima Adams", { exact: false }).first()).toBeVisible();
      await check(page, ti, "bookings", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Week" }).click();
      await expect(page.getByRole("button", { name: /^Sat 2026-10-17: 3 appointments/ })).toBeVisible();
      await check(page, ti, "bookings-week", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Availability" }).click();
      await expect(page.getByRole("group", { name: "Working days" })).toBeVisible();
      await check(page, ti, "bookings-rules", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("bookings: book a free slot and mark one done — same POST bodies", async ({ page }) => {
  await openScreen(page, BOOKINGS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.locator(".vbk-qb")).toHaveAttribute("open", "");      // open on desktop
  await page.getByLabel("Time").selectOption("12:00");
  await page.getByLabel("Service").selectOption("svc-2");
  await page.getByLabel("Customer name").fill("Nomsa Dlamini");
  const booked = page.waitForRequest((r) => r.method() === "POST" && /^\/v1\/bookings\/[^/]+$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Book", exact: true }).click();
  expect((await booked).postDataJSON()).toEqual({ start: "2026-10-17T12:00", service_id: "svc-2", service_name: "Catering consult",
    customer_name: "Nomsa Dlamini", customer_phone: "", notes: "", channel: "dashboard" });
  const done = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/bookings\/[^/]+\/bk-1\/status$/.test(new URL(r.url()).pathname));
  await page.locator(".vbk-appt", { hasText: "Fatima Adams" }).getByRole("button", { name: "Done" }).click();
  expect((await done).postDataJSON()).toEqual({ status: "completed" });
  await page.getByRole("tab", { name: "Week" }).click();
  await page.getByRole("button", { name: /^Sun 2026-10-18/ }).click();
  await expect(page.getByText("Marco Rossi")).toBeVisible();
});

test("bookings on a phone: the day's list comes first; New booking opens the form", async ({ page }) => {
  await openScreen(page, BOOKINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await expect(page.getByLabel("Time")).toBeHidden();
  await page.getByText("New booking").click();
  await expect(page.getByLabel("Time")).toBeVisible();
});

// ── Projects ──
const PROJECTS = { name: "projects", role: "owner", modules: ["projects"], nav: "Operate", tab: "Projects", ready: "Bay Events catering" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`projects ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, PROJECTS, { viewport, theme });
      await expect(page.getByText("Master code library · 2")).toBeVisible();
      await check(page, ti, "projects", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: /^Bay Events catering BE-2026/ }).click();
      await expect(page.getByRole("heading", { name: "Bay Events catering", level: 2 })).toBeVisible();
      await expect(page.getByText("Documents · 10 (with phases)")).toBeVisible();
      await check(page, ti, "projects-open", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Money" }).click();
      await expect(page.getByText(/^R.64.500\.50$/)).toBeVisible();
      await check(page, ti, "projects-money", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("projects: alias, phase and BOQ value send the same bodies; a phone goes back to the list", async ({ page }) => {
  await openScreen(page, PROJECTS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("button", { name: /^Bay Events catering BE-2026/ }).click();
  await expect(page.locator(".vpj-list")).toBeHidden();
  const alias = page.waitForRequest((r) => r.method() === "POST" && /\/p\/pj-1\/aliases$/.test(new URL(r.url()).pathname));
  await page.getByLabel("Add another name for this project").fill("BEC");
  await page.getByLabel("Add another name for this project").press("Enter");
  expect((await alias).postDataJSON()).toEqual({ alias: "BEC" });
  const phase = page.waitForRequest((r) => r.method() === "POST" && /\/p\/pj-1\/phases$/.test(new URL(r.url()).pathname));
  await page.getByLabel("New phase").fill("Phase 3");
  await page.getByRole("button", { name: "Add phase" }).click();
  expect((await phase).postDataJSON()).toEqual({ phase: "Phase 3" });
  await page.getByRole("tab", { name: "BOQ" }).click();
  await expect(page.getByText(/^BOQ · R.120.000\.00$/)).toBeVisible();
  await page.getByLabel("Contract value (R)").fill("1 250 000");
  const boq = page.waitForRequest((r) => r.method() === "POST" && /\/boq$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Save value" }).click();
  expect((await boq).postDataJSON()).toEqual({ total: 1250000 });
  await page.getByRole("button", { name: "All projects" }).click();
  await expect(page.locator(".vpj-list")).toBeVisible();
});

test("projects: search narrows the list, a phase opens with a way to its parent", async ({ page }) => {
  await openScreen(page, PROJECTS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByPlaceholder("Find a project, client or number").fill("market");
  await expect(page.getByRole("button", { name: /^Spring market stall/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Bay Events catering BE-2026/ })).toHaveCount(0);
  await page.getByPlaceholder("Find a project, client or number").fill("");
  await page.getByRole("button", { name: /^Spring gala/ }).click();
  await expect(page.getByRole("button", { name: "Phase of Bay Events catering" })).toBeVisible();
  await expect(page.locator(".vpj-list")).toBeVisible();     // both panes on a desktop
});

// ── Documents ──
const DOCUMENTS = { name: "documents", role: "owner", nav: "Operate", tab: "Documents", ready: "Atlantic-Dairy-invoice-BILL-0031.pdf" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`documents ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, DOCUMENTS, { viewport, theme });
      await expect(page.getByText("9 documents")).toBeVisible();
      await check(page, ti, "documents", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Add" }).click();
      await expect(page.getByRole("heading", { name: "Upload to what Vula knows" })).toBeVisible();
      await check(page, ti, "documents-add", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Tidy up" }).click();
      await expect(page.getByRole("heading", { name: "Documents health" })).toBeVisible();
      await check(page, ti, "documents-tidy", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "What customers see" }).click();
      await expect(page.getByText("2 of 4 are visible to customers", { exact: false })).toBeVisible();
      await check(page, ti, "documents-customers", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("documents: filters go to the server, File under… assigns, Load more continues from what's shown", async ({ page }) => {
  await openScreen(page, DOCUMENTS, { viewport: VIEWPORTS.desktop, theme: "light" });
  const filtered = page.waitForRequest((r) => /\/filed$/.test(new URL(r.url()).pathname) && new URL(r.url()).searchParams.get("category") === "Invoice");
  await page.getByLabel("Category").selectOption("Invoice");
  expect(new URL((await filtered).url()).searchParams.get("offset")).toBe("0");
  await page.getByLabel("Category").selectOption("");
  const assign = page.waitForRequest((r) => r.method() === "POST" && /\/assign-project$/.test(new URL(r.url()).pathname));
  await page.getByLabel("File Menu-spring-2026.pdf under a project").selectOption("Catering");
  expect((await assign).postDataJSON()).toEqual({ tenant_id: expect.any(String), project: "Catering", clickup_list_id: null });
  // a longer library: Load more asks for the page after the documents already shown
  const page9 = Array.from({ length: 9 }, (_, i) => ({ id: `big-${i}`, filename: `Statement-${i + 1}.pdf`, category: "Invoice", project: "Shop", status: "filed", fields: {} }));
  await page.route(/\/filed\?/, (route) => route.fulfill({ json: { documents: page9, total: 30 } }));
  await page.getByLabel("Category").selectOption("Invoice");
  const more = page.waitForRequest((r) => /\/filed$/.test(new URL(r.url()).pathname) && new URL(r.url()).searchParams.get("offset") !== "0");
  await page.getByRole("button", { name: /^Load more/ }).click();
  expect(new URL((await more).url()).searchParams.get("offset")).toBe("9");
});

test("documents: who-can-see toggles and the customer's-eye search use the same calls", async ({ page }) => {
  await openScreen(page, DOCUMENTS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("tab", { name: "What customers see" }).click();
  const aud = page.waitForRequest((r) => r.method() === "POST" && /\/kb\/kb-2\/audience$/.test(new URL(r.url()).pathname));
  await page.locator(".vdc-line", { hasText: "Allergen-guide.docx" }).getByRole("button", { name: "Let customers see it" }).click();
  expect((await aud).postDataJSON()).toEqual({ audience: "public" });
  await page.getByLabel("A customer's question").fill("do you deliver on Saturdays?");
  await page.getByRole("button", { name: "Search as a customer" }).click();
  await expect(page.getByText("We deliver Tuesday to Saturday", { exact: false })).toBeVisible();
});

test("documents: a queued file uploads as one batch, team-only unless ticked", async ({ page }) => {
  await openScreen(page, DOCUMENTS, { viewport: VIEWPORTS.tablet, theme: "light" });
  await page.getByRole("button", { name: "Add documents" }).click();
  await page.locator(".vdc-drop input[type=file]").setInputFiles({ name: "Price-list.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n") });
  await expect(page.locator(".vdc-qrow", { hasText: "Price-list.csv" })).toContainText("Pending");
  const up = page.waitForRequest((r) => r.method() === "POST" && /\/ingest\/batch$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Upload 1 file" }).click();
  const body = (await up).postDataBuffer().toString();
  expect(body).toContain('name="audience"\r\n\r\nstaff');
  expect(body).toContain('filename="Price-list.csv"');
});

// ── Settings ──
const SETTINGS = { name: "settings", role: "owner", nav: "Settings", ready: "Brand kit" };
const settingsSection = async (page, label) => {
  await page.locator(".vst-nav").getByRole("button", { name: label }).click();
  await expect(page.locator("#vst-title")).toHaveText(label);
};

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`settings ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, SETTINGS, { viewport, theme });
      if (vp === "desktop") await expect(page.getByLabel("Business name")).toHaveValue("Harbour & Vine Deli");
      await check(page, ti, "settings", `${vp}-${theme}`, viewport);
      await settingsSection(page, "Brand kit");
      await expect(page.getByLabel("Business name")).toHaveValue("Harbour & Vine Deli");
      await check(page, ti, "settings-brand", `${vp}-${theme}`, viewport);
      if (vp !== "desktop") await page.getByRole("button", { name: "All settings" }).click();
      await settingsSection(page, "Opening hours");
      await expect(page.getByLabel("Fri closes")).toHaveValue("18:00");
      await check(page, ti, "settings-hours", `${vp}-${theme}`, viewport);
      if (vp !== "desktop") await page.getByRole("button", { name: "All settings" }).click();
      await settingsSection(page, "What Vula has learned");
      await expect(page.getByText("Do you deliver to Camps Bay?", { exact: false })).toBeVisible();
      await check(page, ti, "settings-learned", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

const putBody = (page, path) => page.waitForRequest((r) => ["PUT", "POST"].includes(r.method()) && new URL(r.url()).pathname.endsWith(path));

test("settings: brand kit, hours and delivery save the same bodies", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByLabel("Business name").fill("Harbour & Vine");
  await page.getByRole("group", { name: "Corners" }).getByRole("button", { name: "Sharp" }).click();
  const brand = putBody(page, "/admin/invoice-settings");
  await page.getByRole("button", { name: "Save brand kit" }).click();
  const b = (await brand).postDataJSON();
  expect(b).toMatchObject({ trading_as: "Harbour & Vine", corner_style: "sharp", accent_color: "#2C5545", secondary_color: "#C8963E",
    header_cta_text: "Order on WhatsApp", header_sticky: true, logo_size: "md" });

  await settingsSection(page, "Opening hours");
  await page.getByRole("checkbox", { name: "Sun" }).check();
  await page.getByLabel("Sun closes").fill("12:00");
  const hours = putBody(page, "/admin/order-settings");
  await page.getByRole("button", { name: "Save opening hours" }).click();
  const h = (await hours).postDataJSON();
  expect(h.business_hours.sun).toEqual({ open: "08:00", close: "12:00" });
  expect(h.business_hours.fri).toEqual({ open: "07:30", close: "18:00" });
  expect(h.business_hours_note).toBe("Closed on public holidays.");

  await settingsSection(page, "Delivery areas & fees");
  await expect(page.getByLabel("Delivery fee")).toHaveValue("45");
  await page.getByLabel("Delivery fee").fill("52.50");
  const del = putBody(page, "/admin/order-settings");
  await page.getByRole("button", { name: "Save delivery settings" }).click();
  expect((await del).postDataJSON()).toMatchObject({ delivery_fee_cents: 5250, free_delivery_over_cents: 50000, min_order_cents: 15000,
    delivery_areas: ["Sea Point", "Green Point", "Mouille Point", "Gardens"], delivery_radius_km: 6 });
});

test("settings on a phone: the section list, one section, back to the list", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await expect(page.locator(".vst-page")).toBeHidden();
  await settingsSection(page, "Business profile");
  await expect(page.locator(".vst-nav")).toBeHidden();
  const first = page.locator(".vst-q").first();
  await first.locator("textarea").fill("Sea Point, Cape Town");
  await first.getByRole("checkbox", { name: "Customers can see this answer" }).check();
  const prof = putBody(page, "/admin/business-profile");
  await page.getByRole("button", { name: "Save profile" }).click();
  const body = (await prof).postDataJSON();
  expect(Object.values(body.answers)).toContain("Sea Point, Cape Town");
  expect(body.public_keys.length).toBe(1);
  await page.getByRole("button", { name: "All settings" }).click();
  await expect(page.locator(".vst-nav")).toBeVisible();
});

// ── Smart Scanner (Money › Scanner, and Add › Scan) ──
const SCANNER = { name: "scanner", role: "owner", nav: "Money", tab: "Scanner", ready: "Take a photo or choose one", modules: ["scanner"] };   // the Smart Scanner module
// a tiny white PNG stands in for the photo; the mock reads it as a delivery note
const PHOTO = { name: "delivery-note.png", mimeType: "image/png", buffer: Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=", "base64") };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`scanner ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, SCANNER, { viewport, theme });
      await check(page, ti, "scanner", `${vp}-${theme}`, viewport);
      await page.locator("#vsc-file").setInputFiles(PHOTO);
      await expect(page.getByText("Match lines to your products (1/2 found)")).toBeVisible();
      await expect(page.getByText(/^R.1.842\.50$/)).toBeVisible();
      await check(page, ti, "scanner-review", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("scanner: review, then commit, stock and cost prices send the same bodies", async ({ page }) => {
  await openScreen(page, SCANNER, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("tab", { name: "Delivery note" }).click();
  const scan = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/scan$/.test(new URL(r.url()).pathname));
  await page.locator("#vsc-file").setInputFiles(PHOTO);
  const sb = (await scan).postDataJSON();
  expect(sb.doc_type).toBe("delivery_note");
  expect(sb.image_base64).toMatch(/^data:image\/png;base64,/);
  await expect(page.getByText("Supplier \"Atlantic Dairy Co-op\" — 30 day terms")).toBeVisible();

  // the unmatched line: pick a product and a quantity, then book both ticked lines
  await page.getByLabel("Product for Cream cheese tubs 500g").selectOption("p-01");
  await page.getByLabel("Quantity for Cream cheese tubs 500g").fill("5");
  const stock = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/stock\/receive$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Book ticked lines into stock" }).click();
  expect((await stock).postDataJSON()).toEqual({ reference: "DN-55821", lines: [
    { product_id: "p-03", quantity: 12, unit_cost_cents: 7950 }, { product_id: "p-01", quantity: 5, unit_cost_cents: 14808 }] });
  await expect(page.getByRole("status").filter({ hasText: "Booked 2 lines into stock." })).toBeVisible();

  const commit = page.waitForRequest((r) => r.method() === "POST" && /\/scan\/commit$/.test(new URL(r.url()).pathname) && r.postDataJSON()?.auto_commit === true);
  await page.getByRole("button", { name: /^Commit to books · R.1.842\.50$/ }).click();
  expect((await commit).postDataJSON().extracted.total_cents).toBe(184250);
  await expect(page.getByText("Booked R 1 842.50 from Atlantic Dairy Co-op as a bill, due 16 Nov.")).toBeVisible();
  await page.getByRole("button", { name: "Scan another" }).click();
  await expect(page.getByText("Take a photo or choose one")).toBeVisible();
});

test("scanner: Add › Scan opens it in a sheet", async ({ page }) => {
  await openScreen(page, { name: "home", role: "owner", nav: null, ready: "Won in", modules: ["scanner"] }, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "Add" }).click();
  await page.getByRole("dialog", { name: "Add" }).getByText("Scan a document").click();
  const sheet = page.getByRole("dialog", { name: "Smart scan" });
  await expect(sheet.getByText("Take a photo or choose one")).toBeVisible();
  await sheet.locator("#vsc-file").setInputFiles(PHOTO);
  await expect(sheet.getByText("Match lines to your products (1/2 found)")).toBeVisible();
});

// ── Master HQ › Tenants ──
const MASTER = { name: "master", role: "master", nav: "Master", ready: "need attention" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`master tenants ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, MASTER, { viewport, theme });
      await expect(page.getByRole("img", { name: /^Health: Check — 3 unanswered question/ }).first()).toBeVisible();
      await check(page, ti, "master-tenants", `${vp}-${theme}`, viewport);
      await page.getByText("Harbour & Vine Deli", { exact: true }).locator("visible=true").first().click();
      await expect(page.getByRole("dialog", { name: "Harbour & Vine Deli" })).toBeVisible();
      await check(page, ti, "master-tenant-sheet", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`master tenant detail ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, MASTER, { viewport, theme });
      await page.getByText("Harbour & Vine Deli", { exact: true }).locator("visible=true").first().click();
      await page.getByRole("button", { name: "Details" }).click();
      await expect(page.getByRole("heading", { name: "demo-shop" })).toBeVisible();
      await expect(page.getByLabel("Phone number ID")).toBeVisible();
      await check(page, ti, "master-detail", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Usage" }).click();
      await expect(page.getByText("US$6.48")).toBeVisible();
      await page.getByRole("tab", { name: "Conversations" }).click();
      await page.getByText("+27820000101").locator("visible=true").first().click();
      await expect(page.getByRole("list", { name: "Messages with +27820000101" })).toBeVisible();
      await check(page, ti, "master-detail-thread", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Errors" }).click();
      await expect(page.getByText("Media download timed out (Meta 131052)").locator("visible=true").first()).toBeVisible();
      await check(page, ti, "master-detail-errors", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("master tenant detail: linking a number and pushing the catalogue send the same bodies", async ({ page }) => {
  await openScreen(page, MASTER, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.evaluate(() => { window.location.hash = "#/master/tenant/demo-shop"; });
  await expect(page.getByRole("heading", { name: "demo-shop" })).toBeVisible();
  await page.getByLabel("Phone number ID").fill(" 1098765432 ");
  await page.getByLabel("PIN (optional)").fill("123456");
  const link = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/master\/tenants\/demo-shop\/whatsapp-number$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Link number" }).click();
  expect((await link).postDataJSON()).toEqual({ phone_number_id: "1098765432", pin: "123456" });
  const push = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/master\/tenants\/demo-shop\/meta-catalog$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Push products" }).click();
  expect((await push).postDataJSON()).toEqual({});
});

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`master ops ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, MASTER, { viewport, theme });
      await page.getByRole("tab", { name: "Health" }).click();
      await expect(page.getByText("Lease healthy")).toBeVisible();
      await check(page, ti, "master-health", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Usage" }).click();
      await expect(page.getByText(/US\$6\.48 · R/).locator("visible=true").first()).toBeVisible();
      await check(page, ti, "master-usage", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Users" }).click();
      await expect(page.getByText("counter@harbourvine.example.test").locator("visible=true").first()).toBeVisible();
      await check(page, ti, "master-users", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("master health: the login-code template is sent only where it's missing, after asking", async ({ page }) => {
  await openScreen(page, MASTER, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("tab", { name: "Health" }).click();
  const card = page.locator(".vms-form", { has: page.getByRole("heading", { name: "Login code template" }) });
  await expect(card.getByText("Approved", { exact: true })).toBeVisible();
  await expect(card.getByText("Not submitted", { exact: true })).toBeVisible();
  await expect(card.getByText("No WhatsApp account on record for demo-shop-rep")).toBeVisible();
  const posts = [];
  page.on("request", (r) => { if (r.method() === "POST" && /\/v1\/master\/wa-login-template$/.test(new URL(r.url()).pathname)) posts.push(r); });
  await card.getByRole("button", { name: "Submit login template" }).click();
  const ask = page.getByRole("dialog", { name: "Submit login template" });
  await expect(ask.getByText(/on 1 WhatsApp account\? Only this template is sent\./)).toBeVisible();
  await ask.getByRole("button", { name: "Cancel" }).click();
  expect(posts).toHaveLength(0);                                    // Cancel sends nothing
  await card.getByRole("button", { name: "Submit login template" }).click();
  await page.getByRole("dialog", { name: "Submit login template" }).getByRole("button", { name: "Submit", exact: true }).click();
  await expect(card.getByText(/Sent for review on …8607/)).toBeVisible();
  expect(posts).toHaveLength(1);
});

test("master ops: backup, digest, new login and password reset make the same calls", async ({ page }) => {
  await openScreen(page, MASTER, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("tab", { name: "Health" }).click();
  const backup = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/master\/qdrant-backup\/run$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Run backup now" }).click();
  await backup;
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByText("Lowveld Cycle Works: WhatsApp token expired.", { exact: false })).toBeVisible();

  await page.getByRole("tab", { name: "Users" }).click();
  await page.getByRole("button", { name: "New login" }).click();
  await page.getByLabel("Tenant").selectOption("demo-bakery");
  await page.getByLabel("Email").fill(" baker@kloof.example.test ");
  await page.getByLabel("Role").selectOption("manager");
  const create = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/master\/tenants\/demo-bakery\/users$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Create login" }).click();
  expect((await create).postDataJSON()).toEqual({ email: "baker@kloof.example.test", role: "manager" });

  await page.getByRole("button", { name: "Remove counter@harbourvine.example.test" }).click();   // asks first; Cancel removes nothing
  const confirm = page.getByRole("dialog").filter({ hasText: "Remove counter@harbourvine.example.test's login for demo-shop?" });
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Remove counter@harbourvine.example.test" }).click();
  const del = page.waitForRequest((r) => r.method() === "DELETE" && /\/v1\/master\/tenants\/demo-shop\/users\/u-staff$/.test(new URL(r.url()).pathname));
  await confirm.getByRole("button", { name: "Remove" }).click();
  await del;
  await page.getByRole("button", { name: "Reset the password for counter@harbourvine.example.test" }).click();
  await expect(page.getByRole("status").filter({ hasText: "New temp password for counter@harbourvine.example.test: demo-reset-value" })).toBeVisible();
});

const MASTER_TOOLS = [
  // [tab, what shows once it has loaded, capture name]
  ["Onboard", "New signups — not yet provisioned (1)", "master-onboard"],
  ["Knowledge", "Do I need a liquor licence", "master-knowledge"],
  ["Audit", "master_impersonate_tenant", "master-audit"],
  ["Benchmark", "Latest scorecard — worst first", "master-benchmark"],
  ["Conversations", "4 of 128 replies need a look", "master-conversations"],
  ["Models", "Model bake-off", "master-models"],
];
for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`master tools ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, MASTER, { viewport, theme });
      for (const [tab, ready, name] of MASTER_TOOLS) {
        await page.getByRole("tab", { name: tab }).click();
        await expect(page.getByText(ready, { exact: false }).locator("visible=true").first()).toBeVisible();
        await check(page, ti, name, `${vp}-${theme}`, viewport);
      }
      expect(errors).toEqual([]);
    });
  }
}

test("master tools: promote, go-live, benchmark, conversation day, retitle and bake-off send the same requests", async ({ page }) => {
  await openScreen(page, MASTER, { viewport: VIEWPORTS.desktop, theme: "light" });
  const req = (method, re) => page.waitForRequest((r) => r.method() === method && re.test(new URL(r.url()).pathname + new URL(r.url()).search));

  await page.getByRole("tab", { name: "Onboard" }).click();
  await page.getByRole("button", { name: "Provision Muizenberg Surf Co" }).click();
  await expect(page.getByRole("dialog", { name: "New tenant" })).toBeVisible();
  await expect(page.getByLabel("Tenant ID")).toHaveValue("muizenberg-surf-co");
  await page.getByRole("dialog", { name: "New tenant" }).getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("tab", { name: "Knowledge" }).click();
  const first = page.getByRole("listitem").filter({ hasText: "Do I need a liquor licence" });
  await first.getByLabel("Promote to").selectOption("network");
  const promote = req("POST", /\/learned-answers\/la-1\/promote$/);
  await first.getByRole("button", { name: "Promote" }).click();
  expect((await promote).postDataJSON()).toEqual({ target: "network" });

  await page.getByRole("tab", { name: "Benchmark" }).click();
  await page.getByLabel("Tenant").first().selectOption("demo-shop");
  await expect(page.getByText("Invented a figure (R 84 000) not in the books")).toBeVisible();
  const golive = req("POST", /\/v1\/master\/golive-test$/);
  await page.getByRole("button", { name: "Run go-live test" }).click();
  expect((await golive).postDataJSON()).toEqual({ tenant_id: "demo-shop" });
  await page.getByLabel("Only components containing").fill("routing");
  const bench = req("POST", /\/v1\/master\/benchmark$/);
  await page.getByRole("button", { name: "Run benchmark" }).click();
  expect((await bench).postDataJSON()).toEqual({ only: "routing" });
  await page.getByRole("button", { name: /^routing/ }).click();
  await expect(page.getByText("Routed to reasoning, expected commerce_admin")).toBeVisible();

  await page.getByRole("tab", { name: "Conversations" }).click();
  const day = req("GET", /\/conversations\/check\?day=2026-10-15$/);
  await page.getByLabel("Day").fill("2026-10-15");
  await day;
  const conv = page.locator(".vms").filter({ hasText: "Name documents from their content" }).last();
  await conv.getByLabel("Tenant").last().selectOption("demo-build");
  const retitle = req("POST", /\/v1\/master\/documents\/retitle$/);
  await conv.getByRole("button", { name: "Preview" }).last().click();
  expect((await retitle).postDataJSON()).toEqual({ tenant_id: "demo-build", apply: false });

  await page.getByRole("tab", { name: "Models" }).click();
  await page.getByRole("checkbox", { name: "openai/gpt-mini" }).uncheck();
  await page.getByLabel("Skill").selectOption("email_admin");
  const bake = req("POST", /\/v1\/master\/evals\/tools$/);
  await page.getByRole("button", { name: "Run", exact: true }).click();
  expect((await bake).postDataJSON()).toEqual({ models: ["openrouter/anthropic/claude-haiku-5-5", "openrouter/qwen/qwen3-32b"], skill: "email_admin" });
});

test("master tenants: search, create, suspend and billing send the same requests", async ({ page }) => {
  await openScreen(page, MASTER, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByLabel("Search tenants").fill("salon");
  await expect(page.getByRole("row")).toHaveCount(2);   // header + Fynbos Hair Studio
  await page.getByLabel("Search tenants").fill("");

  await page.getByRole("button", { name: "New tenant" }).click();
  await page.getByLabel("Tenant ID").fill("demo-new");
  await page.getByLabel("Display name").fill("New Shop");
  const create = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/v1/tenants");
  await page.getByRole("button", { name: "Create tenant" }).click();
  expect((await create).postDataJSON()).toEqual({ tenant_id: "demo-new", display_name: "New Shop", business_type: "retail" });

  await page.getByRole("tab", { name: "Tenants" }).click();
  await page.getByRole("row", { name: /Kloof Street Bakehouse/ }).click();
  const trial = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/master\/tenants\/demo-bakery\/extend-trial$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "+14 day trial" }).click();
  expect((await trial).postDataJSON()).toEqual({ days: 14 });
  const sus = page.waitForRequest((r) => r.method() === "PATCH" && /\/v1\/master\/tenants\/demo-bakery$/.test(new URL(r.url()).pathname));
  await page.getByRole("dialog", { name: "Kloof Street Bakehouse" }).getByRole("button", { name: "Suspend" }).click();
  await page.getByRole("dialog").filter({ hasText: "Suspend Kloof Street Bakehouse?" }).getByRole("button", { name: "Suspend" }).click();
  expect((await sus).postDataJSON()).toEqual({ active: false });
});

// ── Settings › connections (settings/connect/) ──
const okJson = (body) => (route) => route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });
const CONNECTIONS = [
  ["Payments (Yoco)", "Test — no real money moves"],
  // [section label, what shows once it has loaded, optional routes to install first]
  ["Google (Drive & Gmail)", "Draft-only (you approve & send)", async (page) =>
    page.route(/\/v1\/google\/status\/[^/]+$/, okJson({ status: "connected", email: "owner@harbourvine.example.test" }))],
  ["Microsoft (OneDrive & Outlook)", "Connect Microsoft"],
  ["Your WhatsApp profile", "Apply to WhatsApp"],
  ["ClickUp", "Send missed documents", async (page) => {
    await page.route(/\/v1\/clickup\/status\/[^/]+$/, okJson({ status: "connected", team_id: "9012345", default_list_id: "L2",
      last_synced_at: "2026-10-17T10:05:00+02:00", last_sync_status: "ok" }));
    await page.route(/\/v1\/clickup\/lists\/[^/]+$/, okJson({ lists: [{ id: "L1", name: "Catering jobs" }, { id: "L2", name: "Shop tasks" }] }));
  }],
  ["Email (IMAP / SMTP)", "2 connected", async (page) => page.route(/\/v1\/email\/status\/[^/]+$/, okJson({ accounts: [
    { id: "ma-1", email: "hello@harbourvine.example.test", is_primary: true, send_mode: "draft", last_sync_at: "2026-10-17T09:40:00+02:00" },
    { id: "ma-2", email: "orders@harbourvine.example.test", is_primary: false, send_mode: "send", last_sync_at: null }] }))],
  ["Shopify store", "Live updates on", async (page) => page.route(/\/admin\/shopify$/, okJson({ connected: true, shop_name: "Harbour & Vine",
    shop_domain: "harbourvine.myshopify.com", live_updates: true, scopes: ["read_products", "read_inventory", "read_orders"],
    last_sync_at: "2026-10-17T08:15:00+02:00", last_sync: { products_found: 64, products_created: 2, products_updated: 5, stock_changes: 11 }, sync: {} }))],
  ["WooCommerce shop", "Connect WooCommerce"],
  ["Your shop’s own address", "DNS records to add", async (page) => page.route(/\/admin\/shop-domain$/, okJson({ domain: "shop.harbourvine.example.test",
    state: "waiting_dns", automated: true, instructions: "Please add these two DNS records for shop.harbourvine.example.test …",
    records: [{ type: "CNAME", name: "shop", value: "shops.vula.example.test", ok: true },
      { type: "TXT", name: "_vula-verify.shop", value: "vula-verify=8f3c2a91d7e04b6f", ok: false }] }))],
  ["WhatsApp number", "Not registered", async (page) => page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson({ status: "connected",
    phone_number: "+27 82 000 0001", verified_name: "Harbour & Vine Deli", webhook_registered: false, coexistence: true }))],
];

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`settings connections ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, SETTINGS, { viewport, theme });
      for (const [label, ready, setup] of CONNECTIONS) {
        if (setup) await setup(page);
        if (vp !== "desktop" && await page.getByRole("button", { name: "All settings" }).isVisible()) await page.getByRole("button", { name: "All settings" }).click();
        await settingsSection(page, label);
        await expect(page.locator(".vst-page").getByText(ready, { exact: false }).or(page.locator(".vst-page").getByRole("list", { name: ready })).first()).toBeVisible();
        await check(page, ti, `settings-${label.toLowerCase().replace(/[^a-z]+/g, "-").replace(/^-|-$/g, "").slice(0, 24)}`, `${vp}-${theme}`, viewport);
      }
      expect(errors).toEqual([]);
    });
  }
}

test("settings: Microsoft connect opens the approval window from the same authorize call", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.route(/\/v1\/microsoft\/authorize-url/, okJson({ url: "http://api.vula.test/fake-ms-consent" }));
  await page.context().route(/fake-ms-consent/, (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>consent</title>" }));   // the popup is its own page
  await settingsSection(page, "Microsoft (OneDrive & Outlook)");
  const auth = page.waitForRequest((r) => /\/v1\/microsoft\/authorize-url$/.test(new URL(r.url()).pathname));
  const popup = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Connect Microsoft" }).click();
  expect(new URL((await auth).url()).searchParams.get("tenant_id")).toBeTruthy();
  const win = await popup;
  await win.waitForLoadState();
  expect(win.url()).toBe("http://api.vula.test/fake-ms-consent");
});

test("settings: the WhatsApp profile applies the same body", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.tablet, theme: "light" });
  await settingsSection(page, "Your WhatsApp profile");
  await page.getByLabel("About (the line under your name)").fill("Deli and bakery, Sea Point");
  await expect(page.getByRole("figure", { name: "How your profile looks on WhatsApp" })).toContainText("Deli and bakery, Sea Point");
  await page.getByLabel("Websites (up to 2)").fill("https://harbourvine.example.test\nhttps://order.harbourvine.example.test");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/wa-profile$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Apply to WhatsApp" }).click();
  expect((await post).postDataJSON().profile).toMatchObject({ about: "Deli and bakery, Sea Point",
    websites: ["https://harbourvine.example.test", "https://order.harbourvine.example.test"], email: "hello@harbourvine.example.test" });
});

test("settings: ClickUp — the token way round, and a refused sign-in says so", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.route(/\/v1\/clickup\/status\/[^/]+$/, okJson({ status: "needs_reconnect", last_sync_error: "OAUTH_025 token revoked" }));
  await settingsSection(page, "ClickUp");
  await expect(page.getByRole("alert")).toContainText("ClickUp has stopped accepting Vula's sign-in. (OAUTH_025 token revoked)");
  await expect(page.locator(".vst-page").getByText("Needs reconnecting")).toBeVisible();
  await page.getByRole("button", { name: /Connect with a ClickUp token/ }).click();
  await page.getByLabel("ClickUp API token").fill("  pk_12345_FAKE  ");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/clickup\/connect$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Connect with token" }).click();
  expect((await post).postDataJSON()).toEqual({ tenant_id: expect.any(String), api_token: "pk_12345_FAKE" });
});

test("settings: email — a preset fills the hosts and connect sends the same body", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await settingsSection(page, "Email (IMAP / SMTP)");
  await page.getByRole("button", { name: "Connect a mailbox" }).click();
  await page.getByLabel("Quick setup").selectOption("Zoho");
  await expect(page.getByLabel("IMAP host")).toHaveValue("imap.zoho.com");
  await page.getByLabel("Email address").fill("sales@harbourvine.example.test");
  await page.getByLabel("Password / app password").fill("not-a-real-password");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/email\/connect$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Connect mailbox" }).click();
  expect((await post).postDataJSON()).toMatchObject({ email: "sales@harbourvine.example.test", imap_host: "imap.zoho.com", imap_port: 993,
    smtp_host: "smtp.zoho.com", smtp_port: 465, send_mode: "draft" });
});

test("settings: Shopify and WooCommerce connect send the same bodies", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await settingsSection(page, "WooCommerce shop");
  await page.getByRole("button", { name: "Connect WooCommerce" }).click();
  await page.getByLabel("Shop address").fill("https://www.harbourvine.example.test");
  await page.getByLabel("Consumer key").fill("ck_fake");
  await page.getByLabel("Consumer secret").fill("cs_fake");
  const woo = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/woocommerce\/connect$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Connect WooCommerce" }).click();
  expect((await woo).postDataJSON()).toMatchObject({ store_url: "https://www.harbourvine.example.test", consumer_key: "ck_fake", consumer_secret: "cs_fake" });

  await settingsSection(page, "Shopify store");
  await page.getByRole("button", { name: "Connect Shopify" }).click();
  await page.getByLabel("Store address").fill("harbourvine.myshopify.com");
  await page.getByRole("button", { name: "I have an Admin API access token (older custom app)" }).click();
  await page.getByLabel("Admin API access token").fill("shpat_fake");
  const shop = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/shopify\/connect$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Connect Shopify" }).click();
  const body = (await shop).postDataJSON();
  expect(body).toMatchObject({ shop_domain: "harbourvine.myshopify.com", access_token: "shpat_fake" });
  expect(body.client_secret).toBeUndefined();
});

test("settings: the shop's own address is added with the same body", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "dark" });
  await settingsSection(page, "Your shop’s own address");
  await expect(page.getByLabel("Your shop’s address")).toHaveValue("shop.harbourvine.example.test");   // the suggestion
  await page.getByLabel("Your shop’s address").fill("shop.harbourandvine.example.test");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/shop-domain$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Use this address" }).click();
  expect((await post).postDataJSON()).toEqual({ domain: "shop.harbourandvine.example.test" });
});

test("settings: WhatsApp disconnect asks first, then makes the same call", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.tablet, theme: "light" });
  await page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson({ status: "connected", phone_number: "+27 82 000 0001",
    verified_name: "Harbour & Vine Deli", webhook_registered: true }));
  await settingsSection(page, "WhatsApp number");
  await page.getByRole("button", { name: "Disconnect" }).click();
  const del = page.waitForRequest((r) => r.method() === "DELETE" && /\/v1\/whatsapp\/disconnect\/[^/]+$/.test(new URL(r.url()).pathname));
  await page.getByRole("dialog").getByRole("button", { name: "Yes" }).click();
  await del;
  await expect(page.locator(".vst-page").getByText("Not connected")).toBeVisible();
});

test("settings: WhatsApp not connected — what you need, and both ways in", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson(F.notConnected));
  await settingsSection(page, "WhatsApp number");
  const card = page.locator(".vst-page");
  await expect(card.getByRole("list", { name: "What you need" }).getByRole("listitem")).toHaveCount(3);
  await expect(card.getByText(/Vula goes quiet in that chat/)).toBeVisible();
  // tests run without VITE_FB_CONFIG_ID, so both buttons are there but switched off, and it says why
  await expect(card.getByRole("button", { name: "Use my WhatsApp Business number" })).toBeDisabled();
  await expect(card.getByRole("button", { name: "Connect a new number" })).toBeDisabled();
  await expect(card.getByText(/VITE_FB_(APP|CONFIG)_ID/)).toBeVisible();
});

test("settings: a number shared with the WhatsApp Business app says so", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "dark" });
  await page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson({ ...F.whatsappStatus, coexistence: true }));
  await settingsSection(page, "WhatsApp number");
  await expect(page.locator(".vst-page").getByText("Shared with", { exact: true })).toBeVisible();
  await expect(page.locator(".vst-page").getByText(/reply from your phone any time/)).toBeVisible();
});

const NUMBERS = { ...F.whatsappStatus, purpose: "shop", numbers: [
  { phone_number_id: "P1", phone_number: "+27 82 000 0001", verified_name: "Harbour & Vine Deli", status: "connected",
    purpose: "shop", purpose_label: "Customers: shop and orders", is_primary: true, on_vula_account: false },
  { phone_number_id: "P2", phone_number: "+27 82 000 0002", verified_name: "Harbour & Vine Bookings", status: "connected",
    purpose: "bookings", purpose_label: "Customers: bookings", is_primary: false, on_vula_account: true },
  { phone_number_id: "P3", phone_number: "+27 82 000 0003", verified_name: "Harbour & Vine Team", status: "connected",
    purpose: "team", purpose_label: "Team only", is_primary: false, label: "Stores", on_vula_account: false },
] };

test("settings: several WhatsApp numbers — what each is for, the main one, and adding one", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson(NUMBERS));
  await settingsSection(page, "WhatsApp number");
  const list = page.getByRole("list", { name: "Your WhatsApp numbers" });
  await expect(list.getByRole("listitem")).toHaveCount(3);
  await expect(list.getByRole("listitem").first()).toContainText("Main number");
  await expect(list.getByRole("listitem").nth(2)).toContainText("Stores");
  // the main number can't be team-only; a team line can't be made main
  await expect(page.getByLabel("What +27 82 000 0001 is for").locator('option[value="team"]')).toHaveAttribute("disabled", "");
  await expect(page.getByRole("button", { name: "Make +27 82 000 0003 the main number" })).toHaveCount(0);

  await page.route(/\/v1\/whatsapp\/numbers\/[^/]+\/[^/]+$/, okJson({ phone_number_id: "P3", purpose: "assistant" }));
  const patch = page.waitForRequest((r) => r.method() === "PATCH" && /\/v1\/whatsapp\/numbers\/[^/]+\/P3$/.test(new URL(r.url()).pathname));
  await page.getByLabel("What +27 82 000 0003 is for").selectOption("assistant");
  expect((await patch).postDataJSON()).toEqual({ purpose: "assistant" });

  const main = page.waitForRequest((r) => r.method() === "PATCH" && /\/P2$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Make +27 82 000 0002 the main number" }).click();
  expect((await main).postDataJSON()).toEqual({ is_primary: true });

  await page.getByRole("button", { name: "Add a number" }).click();
  await expect(page.getByLabel("What the new number is for")).toHaveValue("team");
  await expect(page.locator(".vcn-token").getByText(/Only your team is answered here/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Use my WhatsApp Business number" })).toBeDisabled();   // no VITE_FB_CONFIG_ID in tests
  await check(page, test.info(), "settings-wa-numbers", "mobile-light", VIEWPORTS.mobile);
});

test("settings: a number on Vula's account can be moved to the owner's own Meta account", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "dark" });
  await page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson(NUMBERS));
  await settingsSection(page, "WhatsApp number");
  // only P2 is on Vula's own account
  await expect(page.getByRole("button", { name: /to my own Meta account/ })).toHaveCount(1);
  await page.getByRole("button", { name: "Move +27 82 000 0002 to my own Meta account" }).click();
  const steps = page.getByRole("list", { name: "Before you move +27 82 000 0002" });
  await expect(steps.getByRole("listitem")).toHaveCount(4);
  await expect(steps).toContainText("two-step PIN");
  await expect(page.getByRole("button", { name: "Start the move" })).toBeDisabled();   // no VITE_FB_CONFIG_ID in tests
  await check(page, test.info(), "settings-wa-move", "mobile-dark", VIEWPORTS.mobile);
});

test("settings: disconnecting one of several numbers asks about that number only", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.tablet, theme: "dark" });
  await page.route(/\/v1\/whatsapp\/connect\/status\/[^/]+$/, okJson(NUMBERS));
  await settingsSection(page, "WhatsApp number");
  await page.getByRole("button", { name: "Disconnect +27 82 000 0001" }).click();
  await expect(page.getByRole("dialog")).toContainText("Disconnect +27 82 000 0001 from Vula?");
  await expect(page.getByRole("dialog")).toContainText("Another of your numbers becomes the main one.");
  const del = page.waitForRequest((r) => r.method() === "DELETE" && /\/v1\/whatsapp\/disconnect\/[^/]+$/.test(new URL(r.url()).pathname));
  await page.getByRole("dialog").getByRole("button", { name: "Yes" }).click();
  expect(new URL((await del).url()).searchParams.get("phone_number_id")).toBe("P1");
  await expect(page.getByRole("list", { name: "Your WhatsApp numbers" })).toBeVisible();      // still connected
});

test("settings: Yoco — update keys sends the same body; test uses the same call", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await settingsSection(page, "Payments (Yoco)");
  await expect(page.locator(".vst-page").getByText("Connected · test mode")).toBeVisible();
  const test = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/yoco\/test\/[^/]+$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Test connection" }).click();
  await test;
  await page.getByRole("button", { name: "Update keys" }).click();
  await page.getByLabel("Secret key").fill("  sk_test_fake  ");
  await page.getByRole("checkbox", { name: /Register Vula’s webhook/ }).uncheck();
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/yoco\/connect$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Update keys" }).click();
  expect((await post).postDataJSON()).toMatchObject({ secret_key: "sk_test_fake", public_key: null, auto_register_webhook: false });
});

test("settings: a Microsoft sync that is failing says so", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.route(/\/v1\/microsoft\/status\/[^/]+$/, okJson({ status: "connected", email: "o@x.example.test",
    last_synced_at: "2026-10-17T06:30:00+02:00", last_sync_status: "error", last_sync_error: "invalid_grant" }));
  await settingsSection(page, "Microsoft (OneDrive & Outlook)");
  await expect(page.getByText("Failing — 4h ago · invalid_grant")).toBeVisible();
});

// ── Bank & matching ──
const BANK = { name: "bank", role: "owner", nav: "Money", tab: "Bank & matching", ready: "ATLANTIC DAIRY COOP DEBIT ORDER" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`bank ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, BANK, { viewport, theme });
      await expect(page.getByText(/^R.18.742\.50$/)).toBeVisible();
      await check(page, ti, "bank", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: "Group by payee" }).click();
      await expect(page.getByRole("button", { name: "Apply to all 6" })).toBeVisible();
      await check(page, ti, "bank-groups", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: "Tools" }).click();
      await expect(page.getByRole("dialog", { name: "Statement tools" })).toBeVisible();
      await check(page, ti, "bank-tools", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

const bankPost = (page, re) => page.waitForRequest((r) => r.method() === "POST" && re.test(new URL(r.url()).pathname));

test("bank: category, worker, match and ignore send the same bodies", async ({ page }) => {
  await openScreen(page, BANK, { viewport: VIEWPORTS.mobile, theme: "light" });
  const cat = bankPost(page, /\/bank\/transactions\/bt-3\/categorize$/);
  await page.getByLabel("Category for CASH SEND LWAZI").selectOption("rent");
  expect((await cat).postDataJSON()).toEqual({ account_code: "rent" });
  const wk = bankPost(page, /\/bank\/transactions\/bt-3\/worker$/);
  await page.getByLabel("Worker for CASH SEND LWAZI").selectOption("wk-2");
  expect((await wk).postDataJSON()).toEqual({ worker_id: "wk-2" });
  const match = bankPost(page, /\/bank\/transactions\/bt-1\/match$/);
  const picker = page.getByLabel("Match invoice for PAYMENT RECEIVED BAY EVENTS CO INV-0041");
  await picker.focus();
  await expect(picker.locator("option", { hasText: "INV-0041" })).toHaveCount(1);
  await picker.selectOption("inv-0041");
  expect((await match).postDataJSON()).toEqual({ action: "match", invoice_id: "inv-0041" });
  const ign = bankPost(page, /\/bank\/transactions\/bt-2\/match$/);
  await page.locator(".vbr-txn", { hasText: "ATLANTIC DAIRY COOP DEBIT ORDER" }).getByRole("button", { name: "Ignore" }).click();
  expect((await ign).postDataJSON()).toEqual({ action: "ignore" });
});

test("bank: filters ask the server; a payee group applies one category to all its lines", async ({ page }) => {
  await openScreen(page, BANK, { viewport: VIEWPORTS.desktop, theme: "light" });
  const f = page.waitForRequest((r) => /\/bank\/transactions$/.test(new URL(r.url()).pathname) && new URL(r.url()).searchParams.get("status") === "matched");
  await page.getByRole("button", { name: "Matched", exact: true }).click();
  await f;
  await expect(page.locator(".vbr-txn")).toHaveCount(2);
  await page.getByRole("button", { name: "Group by payee" }).click();
  const group = page.locator(".vbr-card", { hasText: "CASH SEND LWAZI" });
  await group.getByLabel("Category for CASH SEND LWAZI").selectOption("cost_of_sales");
  const bulk = bankPost(page, /\/bank\/transactions\/bulk-categorize$/);
  await group.getByRole("button", { name: "Apply to all 6" }).click();
  expect((await bulk).postDataJSON()).toEqual({ txn_ids: ["bt-3"], account_code: "cost_of_sales", project: null, trade: null });
});

// ── Payments gateways + Accounts ──
const PAYMENTS = { name: "payments", role: "owner", nav: "Money", tab: "Payments", ready: "Peach Payments" };
const BOOKS = { name: "books", role: "owner", nav: "Money", tab: "Accounts", ready: "Net profit" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`payments ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, PAYMENTS, { viewport, theme });
      await expect(page.getByText("Default", { exact: true })).toBeVisible();
      await check(page, ti, "payments", `${vp}-${theme}`, viewport);
      await page.locator(".vpy-card", { hasText: "PayFast" }).getByRole("button", { name: "Connect" }).click();
      await expect(page.getByRole("dialog", { name: "Connect PayFast" })).toBeVisible();
      await check(page, ti, "payments-connect", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
    test(`books ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, BOOKS, { viewport, theme });
      await expect(page.getByText(/^R.63.777\.00$/)).toBeVisible();
      await expect(page.getByText("VAT payable to SARS")).toBeVisible();
      await check(page, ti, "books", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("payments: connecting a gateway sends its labelled keys; default and disconnect use the same calls", async ({ page }) => {
  await openScreen(page, PAYMENTS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.locator(".vpy-card", { hasText: "PayFast" }).getByRole("button", { name: "Connect" }).click();
  const dlg = page.getByRole("dialog", { name: "Connect PayFast" });
  await dlg.getByLabel("Merchant ID").fill("merchant-0001");
  await dlg.getByLabel("Merchant key").fill("fake-merchant-key");
  await dlg.getByLabel("Passphrase").fill("fake-passphrase");
  await expect(dlg.getByLabel("Merchant key")).toHaveAttribute("type", "password");
  await dlg.getByLabel("Mode").selectOption("test");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/payments\/[^/]+\/providers$/.test(new URL(r.url()).pathname));
  await dlg.getByRole("button", { name: "Connect" }).click();
  expect((await post).postDataJSON()).toEqual({ provider: "payfast", mode: "test", is_default: false,
    credentials: { merchant_id: "merchant-0001", merchant_key: "fake-merchant-key", passphrase: "fake-passphrase" } });
  const def = page.waitForRequest((r) => r.method() === "POST" && /\/providers\/ozow\/default$/.test(new URL(r.url()).pathname));
  await page.locator(".vpy-card", { hasText: "Ozow" }).getByRole("button", { name: "Make default" }).click();
  await def;
  const del = page.waitForRequest((r) => r.method() === "DELETE" && /\/providers\/ozow$/.test(new URL(r.url()).pathname));
  await page.locator(".vpy-card", { hasText: "Ozow" }).getByRole("button", { name: "Disconnect" }).click();
  await del;
});

test("books: a date range goes to both reports; the ledger export downloads", async ({ page }) => {
  await openScreen(page, BOOKS, { viewport: VIEWPORTS.desktop, theme: "light" });
  const pnl = page.waitForRequest((r) => /\/reports\/pnl$/.test(new URL(r.url()).pathname) && new URL(r.url()).searchParams.get("since") === "2026-10-01");
  await page.getByLabel("From").fill("2026-10-01");
  await pnl;
  await page.route(/\/reports\/ledger\.csv/, (route) => route.fulfill({ status: 200, contentType: "text/csv", body: "date,account,amount\n" }));
  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export for accountant (CSV)" }).click();
  expect((await dl).suggestedFilename()).toBe("ledger_2026-10-01_.csv");
});

// ── Finances (insights, sales, ledger) ──
const FINANCES = { name: "finances", role: "owner", nav: "Money", tab: "Finances", ready: "Financial insights" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`finances ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, FINANCES, { viewport, theme });
      await expect(page.getByRole("img", { name: /^Revenue per day — last 30 days: R.167.470\.00 over 30 days/ })).toBeVisible();
      await check(page, ti, "finances", `${vp}-${theme}`, viewport);
      await page.getByRole("tab", { name: "Ledger" }).click();
      await page.getByRole("button", { name: /^Catering/ }).click();
      await expect(page.getByText("Progress claims (interim payment certificates)")).toBeVisible();
      await page.getByRole("button", { name: "General ledger (trial balance)" }).click();
      await expect(page.getByRole("row", { name: /^Bank · asset R.187.425\.00/ })).toBeVisible();   // cents, not 100× (the old bug)
      await check(page, ti, "finances-ledger", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("finances: the chart has a table view; periods and the CSV export behave as before", async ({ page }) => {
  await openScreen(page, FINANCES, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("button", { name: "Show as table" }).click();
  await expect(page.getByRole("row", { name: /^17 Oct 7 R.6.650\.00$/ })).toBeVisible();
  const r7 = page.waitForRequest((r) => /\/admin\/reports$/.test(new URL(r.url()).pathname) && new URL(r.url()).searchParams.get("days") === "7");
  await page.getByRole("group", { name: "Days" }).getByRole("button", { name: "7 days" }).click();
  await r7;
  const ins = page.waitForRequest((r) => /\/finances\/insights$/.test(new URL(r.url()).pathname) && new URL(r.url()).searchParams.get("days") === "90");
  await page.getByRole("group", { name: "Period" }).getByRole("button", { name: "90 days" }).click();
  await ins;
});

test("finances ledger: budget, a new claim and certify send the same bodies", async ({ page }) => {
  await openScreen(page, FINANCES, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("tab", { name: "Ledger" }).click();
  await page.getByRole("button", { name: "Set the budget for Shop" }).click();
  await page.getByLabel("Budget for Shop (R)").fill("15000");
  const bud = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/projects\/[^/]+\/budget$/.test(new URL(r.url()).pathname));
  await page.getByLabel("Budget for Shop (R)").press("Enter");
  expect((await bud).postDataJSON()).toEqual({ project: "Shop", budget: 15000 });
  await page.getByRole("button", { name: /^Catering/ }).click();
  await page.getByLabel("New claim — work done to date (R)").fill("6400000");
  const claim = page.waitForRequest((r) => r.method() === "POST" && /\/p\/Catering\/claims$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Add claim" }).click();
  expect((await claim).postDataJSON()).toEqual({ cumulative_value: 6400000, retention_pct: 5 });
  const cert = page.waitForRequest((r) => r.method() === "POST" && /\/claims\/cl-2\/certify$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Certify" }).click();
  await cert;
});

// ── Finances › Ledger: job costing + cross-check, as a project business ──
// Routed in after the screen opens (Playwright tries the newest route first); the Ledger tab
// mounts both, so they read the project profile and the fuller fixtures.
async function asProjectBusiness(page) {
  const ok = (body) => (route) => route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(body) });
  await page.route(/vula\.test\/v1\/tenants\/[^/?]+$/, ok({ ...F.tenant, profile: { ...F.tenant.profile, uses_projects: true } }));
  await page.route(/\/admin\/projects\/costing$/, ok(F.jobCosting));
  await page.route(/\/admin\/crosscheck$/, ok(F.crossCheckFull));
  await page.route(/\/admin\/reports\/vat-scenario$/, ok(F.vatScenario));
}

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`job costing + cross-check ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, FINANCES, { viewport, theme });
      await asProjectBusiness(page);
      await page.getByRole("tab", { name: "Ledger" }).click();
      await expect(page.getByRole("heading", { name: "Job costing — is each project making its fee?" })).toBeVisible();
      await page.getByRole("button", { name: /^Bo-Kaap shopfit/ }).click();
      await expect(page.getByRole("list", { name: "Cost by trade for Bo-Kaap shopfit" }).getByText(/^R.163.000$/)).toBeVisible();
      await page.getByRole("button", { name: /^Supplier bills not marked paid/ }).click();
      await expect(page.getByRole("region", { name: "VAT per month" })).toBeVisible();
      await page.getByRole("heading", { name: "Job costing — is each project making its fee?" }).scrollIntoViewIfNeeded();
      await check(page, ti, "jobcosting", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("job costing + cross-check: fee, price advice, book and match send the same requests", async ({ page }) => {
  await openScreen(page, FINANCES, { viewport: VIEWPORTS.mobile, theme: "light" });
  await asProjectBusiness(page);
  await page.getByRole("tab", { name: "Ledger" }).click();
  await page.getByRole("button", { name: /^Clifton house/ }).click();
  await page.getByLabel("Fee % for Clifton house").fill("12.5");
  const fee = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/projects\/terms$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Save fee" }).click();
  expect((await fee).postDataJSON()).toEqual({ project: "Clifton house", fee_pct: 12.5 });

  await page.getByLabel("Item or work").fill("ceiling board");
  await page.getByLabel("Quantity").fill("40");
  await page.getByLabel("Unit").fill("m2");
  const adv = page.waitForRequest((r) => /\/admin\/projects\/price-advice$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Price it" }).click();
  expect(Object.fromEntries(new URL((await adv).url()).searchParams)).toEqual({ item: "ceiling board", quantity: "40", unit: "m2" });

  await page.getByRole("button", { name: /^Bills filed but not in the books/ }).click();
  const book = page.waitForRequest((r) => r.method() === "POST" && /\/crosscheck\/book-unbooked$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Book them" }).click();
  await book;
  await page.getByRole("button", { name: /^Supplier bills not marked paid/ }).click();
  const match = page.waitForRequest((r) => r.method() === "POST" && /\/bank\/transactions\/tx-77\/match$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Yes, Atlantic Electrical was paid on 2026-10-06" }).click();
  expect((await match).postDataJSON()).toEqual({ action: "match", invoice_id: "bi-1" });
});

test("a business without projects shows no empty job-costing box", async ({ page }) => {
  await openScreen(page, FINANCES, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("tab", { name: "Ledger" }).click();
  await expect(page.getByRole("heading", { name: /^Cross-check/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Job costing/ })).toHaveCount(0);
});

// ── Sell › Discounts, Subscriptions, Delivery ──
const DISCOUNTS = { name: "discounts", role: "owner", nav: "Sell", tab: "Discounts", ready: "SPRING15" };
const RECURRING = { name: "recurring", role: "owner", nav: "Sell", tab: "Subscriptions", ready: "Pieter van der Berg" };
const DELIVERY = { name: "delivery", role: "owner", nav: "Sell", tab: "Delivery", ready: "ORD-1051" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`discounts ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, DISCOUNTS, { viewport, theme });
      await expect(page.getByRole("switch", { name: "FREESHIP is off" })).toBeVisible();
      await check(page, ti, "discounts", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: "Edit SPRING15" }).click();
      await expect(page.getByRole("dialog", { name: "Edit SPRING15" })).toBeVisible();
      await check(page, ti, "discounts-edit", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
    test(`recurring ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, RECURRING, { viewport, theme });
      await expect(page.getByText(/^R.175\.00$/)).toBeVisible();   // 2 × R65 + R45 delivery
      await check(page, ti, "recurring", `${vp}-${theme}`, viewport);
      await page.getByRole("button", { name: "New recurring order" }).click();
      await expect(page.getByRole("dialog", { name: "New recurring order" })).toBeVisible();
      await check(page, ti, "recurring-new", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
    test(`delivery ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, DELIVERY, { viewport, theme });
      await expect(page.getByRole("heading", { name: /^Morning/ })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Order workflow" })).toBeVisible();
      await check(page, ti, "delivery", `${vp}-${theme}`, viewport);
      expect(errors).toEqual([]);
    });
  }
}

test("discounts: a fixed amount is sent in cents; on/off and edit use the same calls", async ({ page }) => {
  await openScreen(page, DISCOUNTS, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("button", { name: "Add discount code" }).click();
  const dlg = page.getByRole("dialog", { name: "New discount code" });
  await dlg.getByLabel("Code").fill("braai25");
  await dlg.getByLabel("Kind").selectOption("fixed");
  await dlg.getByLabel("Amount off (R)").fill("25.50");
  await dlg.getByLabel("Minimum order (R, optional)").fill("200");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/admin\/discount-codes$/.test(new URL(r.url()).pathname));
  await dlg.getByRole("button", { name: "Add code" }).click();
  expect((await post).postDataJSON()).toEqual({ code: "BRAAI25", type: "fixed", value: 2550, min_order_cents: 20000, starts_at: null, ends_at: null,
    usage_limit: null, active: true, first_order_only: false, per_customer_limit: null });
  const off = page.waitForRequest((r) => r.method() === "PATCH" && /\/discount-codes\/dc-1$/.test(new URL(r.url()).pathname));
  await page.getByRole("switch", { name: "SPRING15 is on" }).click();
  expect((await off).postDataJSON()).toEqual({ active: false });
});

test("recurring: a new order sends its items with server prices; pause uses the same call", async ({ page }) => {
  await openScreen(page, RECURRING, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("button", { name: "New recurring order" }).click();
  const dlg = page.getByRole("dialog", { name: "New recurring order" });
  await dlg.getByLabel("Customer name").fill("Nomsa Dlamini");
  await dlg.getByLabel("Customer WhatsApp number").fill("27820000199");
  await dlg.getByLabel("Product 1").selectOption("p-02");
  await dlg.getByLabel("Quantity 1").fill("2");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/v1\/subscriptions\/[^/]+$/.test(new URL(r.url()).pathname));
  await dlg.getByRole("button", { name: "Create recurring order" }).click();
  expect((await post).postDataJSON()).toEqual({ customer_name: "Nomsa Dlamini", customer_phone: "27820000199", cadence: "weekly", payment_method: "cod",
    items: [{ product_id: "p-02", product_name: "Sourdough loaf", quantity: 2, unit_price_cents: 6500 }] });
  const pause = page.waitForRequest((r) => r.method() === "POST" && /\/rs-1\/status$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Pause Pieter van der Berg's order" }).click();
  expect((await pause).postDataJSON()).toEqual({ status: "paused" });
});

test("delivery: the run sheet escapes what customers typed (no script from a name or address)", async ({ page }) => {
  await openScreen(page, DELIVERY, { viewport: VIEWPORTS.desktop, theme: "light" });
  const evil = '<img src=x onerror="window.__pwned=1">';
  await page.route(/\/admin\/delivery-list/, (route) => route.fulfill({ json: { date: "2026-10-17", total: 1, paid_count: 0, unpaid_count: 1,
    paid_revenue_cents: 0, unpaid_revenue_cents: 1000, orders: [{ id: "x1", display_id: "ORD-9", delivery_slot: "morning", status: "pending_payment",
      customer_name: evil, customer_phone: "+27820000001", delivery_address: `<script>window.__pwned=2</script>`, total_cents: 1000,
      commerce_order_items: [{ product_name: "<b>bold</b>", quantity: 1 }] }] } }));
  await page.getByLabel("Day").fill("2026-10-18");
  await expect(page.getByText(evil)).toBeVisible();     // shown as text on the page too
  await page.evaluate(() => {
    window.__printed = "";
    window.open = () => ({ document: { write: (h) => { window.__printed += h; }, close() {} }, print() {} });
  });
  await page.getByRole("button", { name: "Print run sheet" }).click();
  const html = await page.evaluate(() => window.__printed);
  expect(html).toContain("&lt;img src=x onerror=&quot;window.__pwned=1&quot;&gt;");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<img");
  expect(html).not.toContain("<script>");
  expect(html).toContain("&lt;b&gt;bold&lt;/b&gt; ×1");
});

test("delivery: workflow settings save the same body", async ({ page }) => {
  await openScreen(page, DELIVERY, { viewport: VIEWPORTS.mobile, theme: "light" });
  const put = page.waitForRequest((r) => r.method() === "PUT" && /\/admin\/order-settings$/.test(new URL(r.url()).pathname));
  await page.getByRole("checkbox", { name: "Pay on delivery" }).click();
  expect((await put).postDataJSON().payment_methods).toEqual(["yoco", "eft", "cod"]);   // the fixture has yoco + eft; ticking adds cod
});
