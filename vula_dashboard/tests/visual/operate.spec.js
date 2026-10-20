// operate.spec.js — Operate › Stock on the redesign's components (UX redesign Phase F,
// 2026-10-20). UI_V2=1 runs the redesign: each screen GATES on axe (serious/critical = fail)
// and on no sideways scroll at 390px. The flag-off run keeps one smoke test of the current look,
// which now runs on the same shared hooks (stock/useStock.js).
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";
import * as F from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

const STOCK = { name: "stock", role: "owner", nav: "Operate", tab: "Stock", ready: "Start a stock-take" };
const SOURDOUGH = "6001234500021";   // fixtures: the lookup answers p-02 for this code, 404 for any other

/** Requests to a path, as parsed JSON bodies. */
function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => { if (r.method() === method && re.test(new URL(r.url()).pathname)) seen.push(r.postDataJSON()); });
  return seen;
}

test.describe("redesign", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`stock ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, STOCK, { viewport, theme });
        await expect(page.getByText("Month end — cold room").locator("visible=true").first()).toBeVisible();
        await check(page, ti, "stock", `${vp}-${theme}`, viewport);

        await page.getByText("Month end — cold room").locator("visible=true").first().click();
        await page.getByLabel("Barcode", { exact: true }).fill(SOURDOUGH);
        await page.locator(".vst-manual").getByRole("button", { name: "Add" }).click();
        await expect(page.getByLabel("Quantity for Sourdough loaf")).toHaveValue("1");
        await check(page, ti, "stock-count", `${vp}-${theme}`, viewport);

        await page.getByRole("button", { name: "Review count" }).click();
        await expect(page.getByText("Aged cheddar (250g)").locator("visible=true").first()).toBeVisible();
        await check(page, ti, "stock-review", `${vp}-${theme}`, viewport);

        await page.getByRole("tab", { name: "Receive" }).click();
        await page.getByLabel("Purchase order").selectOption("po-1");
        await expect(page.getByLabel("Quantity for Aged cheddar (250g)")).toBeVisible();
        await check(page, ti, "stock-receive", `${vp}-${theme}`, viewport);

        await page.getByRole("tab", { name: "Quick adjust" }).click();
        await page.getByRole("searchbox").fill("cheddar");
        await page.getByRole("button", { name: /Aged cheddar/ }).click();
        await expect(page.getByRole("list", { name: "Stock history" })).toBeVisible();
        await check(page, ti, "stock-adjust", `${vp}-${theme}`, viewport);
        expect(errors).toEqual([]);
      });
    }
  }

  test("stock-take: scans, a typed count, an unknown barcode and apply send the same calls", async ({ page }) => {
    await openScreen(page, STOCK, { viewport: VIEWPORTS.mobile, theme: "light" });
    const scans = capture(page, "POST", /\/stock\/counts\/sc-1\/scans$/);
    const links = capture(page, "POST", /\/products\/p-\d+\/barcode$/);
    const applied = capture(page, "POST", /\/stock\/counts\/sc-1\/apply$/);
    await page.getByText("Month end — cold room").locator("visible=true").first().click();

    await page.getByLabel("Barcode", { exact: true }).fill(SOURDOUGH);
    await page.locator(".vst-manual").getByRole("button", { name: "Add" }).click();
    await expect(page.getByLabel("Quantity for Sourdough loaf")).toHaveValue("1");
    await page.getByLabel("Quantity for Sourdough loaf").fill("2.5");
    await expect.poll(() => scans.flatMap((b) => b.scans).length).toBe(2);
    const sent = scans.flatMap((b) => b.scans);
    expect(sent[0]).toMatchObject({ product_id: "p-02", variant_id: null, add: 1 });
    expect(sent[1]).toMatchObject({ product_id: "p-02", variant_id: null, set: 2.5 });
    expect(new Set(sent.map((x) => x.scan_id)).size).toBe(2);

    // a code no product carries: link it to a product, and it counts
    await page.getByLabel("Barcode", { exact: true }).fill("6009000000999");
    await page.locator(".vst-manual").getByRole("button", { name: "Add" }).click();
    const linker = page.getByRole("group", { name: "Link a new barcode" });
    await expect(linker).toContainText("6009000000999");
    await linker.getByRole("searchbox").fill("tapenade");
    await linker.getByRole("button", { name: /Olive tapenade/ }).click();
    await expect.poll(() => links.length).toBe(1);
    expect(links[0]).toEqual({ barcode: "6009000000999" });
    await expect(page.getByLabel("Quantity for Olive tapenade (150g)")).toHaveValue("1");

    await page.getByRole("button", { name: "Review count" }).click();
    await page.getByRole("button", { name: "Apply to stock" }).click();
    await page.getByRole("dialog").getByRole("button", { name: /OK|Confirm|Yes|Continue/ }).first().click();
    await expect.poll(() => applied.length).toBe(1);
    await expect(page.getByText("Applied 3 items.")).toBeVisible();
  });

  test("receive and quick adjust send the same bodies", async ({ page }) => {
    await openScreen(page, STOCK, { viewport: VIEWPORTS.desktop, theme: "light" });
    const received = capture(page, "POST", /\/stock\/receive$/);
    const adjusted = capture(page, "POST", /\/stock\/adjust$/);

    await page.getByRole("tab", { name: "Receive" }).click();
    await page.getByLabel("Purchase order").selectOption("po-1");
    await page.getByLabel("Delivery note / invoice number").fill("DN-7781");
    await page.getByRole("button", { name: "Everything arrived as ordered" }).click();
    await page.getByLabel("Quantity for Sourdough loaf").fill("5");
    await page.getByRole("button", { name: "Book into stock" }).click();
    await expect.poll(() => received.length).toBe(1);
    expect(received[0]).toEqual({ po_id: "po-1", reference: "DN-7781", lines: [
      { product_id: "p-03", variant_id: null, quantity: 12, unit_cost_cents: 6_200 },
      { product_id: "p-02", variant_id: null, quantity: 5, unit_cost_cents: 3_100 },
    ] });
    await expect(page.getByText("Booked 2 lines into stock.")).toBeVisible();

    await page.getByRole("tab", { name: "Quick adjust" }).click();
    await page.getByRole("searchbox").fill("cheddar");
    await page.getByRole("button", { name: /Aged cheddar/ }).click();
    await page.getByLabel("Quantity", { exact: true }).fill("4");
    await page.getByRole("group", { name: "Aged cheddar (250g)" }).getByRole("button", { name: "Add" }).click();
    await expect.poll(() => adjusted.length).toBe(1);
    expect(adjusted[0]).toEqual({ product_id: "p-03", variant_id: null, add: 4 });
    await expect(page.getByText("Saved — stock is now 7.")).toBeVisible();
    await page.getByLabel("Quantity", { exact: true }).fill("10");
    await page.getByRole("button", { name: "Set to" }).click();
    await expect.poll(() => adjusted.length).toBe(2);
    expect(adjusted[1]).toEqual({ product_id: "p-03", variant_id: null, set: 10 });
  });
});

const SUPPLIERS = { name: "suppliers", role: "owner", nav: "Operate", tab: "Suppliers", ready: "Atlantic Dairy Co-op" };

test.describe("redesign: suppliers", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`suppliers ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, SUPPLIERS, { viewport, theme });
        await expect(page.getByRole("list", { name: "Purchase orders" })).toBeVisible();
        await check(page, ti, "suppliers", `${vp}-${theme}`, viewport);
        await page.getByText("Cape Charcuterie", { exact: true }).locator("visible=true").first().click();
        await expect(page.getByRole("dialog", { name: "Edit Cape Charcuterie" })).toBeVisible();
        await check(page, ti, "suppliers-edit", `${vp}-${theme}`, viewport);
        expect(errors).toEqual([]);
      });
    }
  }

  test("suppliers: add, edit, delete and purchase orders send the same calls", async ({ page }) => {
    await openScreen(page, SUPPLIERS, { viewport: VIEWPORTS.mobile, theme: "light" });
    const saved = capture(page, "POST", /\/admin\/suppliers$/);
    const created = capture(page, "POST", /\/admin\/purchase-orders$/);
    const sent = capture(page, "POST", /\/purchase-orders\/po-3\/send$/);
    const moved = [];
    page.on("request", (r) => { if (r.method() === "PATCH") moved.push([new URL(r.url()).pathname.split("/").at(-2), r.postDataJSON()]); });
    const deleted = [];
    page.on("request", (r) => { if (r.method() === "DELETE") deleted.push(new URL(r.url()).pathname); });

    await page.getByRole("button", { name: "Add supplier" }).click();
    const sheet = page.getByRole("dialog", { name: "Add supplier" });
    await sheet.getByLabel("Supplier name").fill("  Overberg Mills ");
    await sheet.getByLabel("Payment terms (days)").fill("7");
    await sheet.getByLabel("Other names on their bills").fill("Overberg, OM Flour");
    await page.getByRole("button", { name: "Add supplier" }).last().click();
    await expect.poll(() => saved.length).toBe(1);
    expect(saved[0]).toMatchObject({ name: "Overberg Mills", payment_terms_days: 7, aliases: ["Overberg", "OM Flour"] });
    expect(saved[0].id).toBeUndefined();

    await page.getByText("Atlantic Dairy Co-op", { exact: true }).locator("visible=true").first().click();
    const edit = page.getByRole("dialog", { name: "Edit Atlantic Dairy Co-op" });
    await expect(edit.getByLabel("Other names on their bills")).toHaveValue("Atlantic Dairy, ADC");
    await edit.getByLabel("Payment terms (days)").fill("45");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect.poll(() => saved.length).toBe(2);
    expect(saved[1]).toMatchObject({ id: "sup-1", name: "Atlantic Dairy Co-op", payment_terms_days: 45, aliases: ["Atlantic Dairy", "ADC"] });

    await page.getByText("Cape Charcuterie", { exact: true }).locator("visible=true").first().click();
    await page.getByRole("button", { name: "Delete supplier" }).click();
    await page.getByRole("dialog").filter({ hasText: 'Delete supplier "Cape Charcuterie"?' }).getByRole("button", { name: "Yes" }).click();
    await expect.poll(() => deleted).toEqual([`/v1/commerce/${F.TENANT_ID}/admin/suppliers/sup-2`]);
    await expect(page.getByRole("dialog", { name: "Edit Cape Charcuterie" })).toHaveCount(0);

    await page.getByRole("button", { name: "Create PO" }).click();
    await expect.poll(() => created.length).toBe(1);
    expect(created[0]).toEqual({ supplier_id: "sup-1", supplier_name: "Atlantic Dairy Co-op",
      items: [{ product_id: "p-03", name: "Aged cheddar (250g)", quantity: 12, unit_cost_cents: 6_200 }] });

    await page.getByLabel("Send Cape Charcuterie by").selectOption("whatsapp");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]).toEqual({ channel: "whatsapp" });
    await expect(page.getByText("Sent via email", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Mark received" }).click();
    await expect.poll(() => moved.length).toBe(1);
    expect(moved[0]).toEqual(["po-1", { status: "received" }]);
  });
});

test("current look: Stock still counts a scan on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...STOCK, nav: "Sell" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("button", { name: "Continue" }).first().click();
  await page.getByPlaceholder("…or type / use a USB scanner").fill(SOURDOUGH);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Sourdough loaf")).toBeVisible();
  expect(F.stockCounts.length).toBe(2);
});
