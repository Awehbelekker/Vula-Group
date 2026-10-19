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
