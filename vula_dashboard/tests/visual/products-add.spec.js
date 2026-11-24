// products-add.spec.js — adding a product with its photo, stock, codes and sizes/colours in one
// go, and making a product's versions from its options in the editor (24 Nov 2026). What goes to
// the API is checked; phone and desktop: axe clean on the new parts, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { VIEWPORTS, openScreen } from "./screens.js";

const PRODUCTS = { name: "products", role: "owner", nav: "Sell", tab: "Products", ready: "Sourdough loaf" };
const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" };
const json = (body, status = 200) => ({ status, contentType: "application/json", headers: cors, body: JSON.stringify(body) });
const serious = async (page, sel) => (await new AxeBuilder({ page }).include(sel).analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`a product is added with its sizes, colours and codes — ${vp}`, async ({ page }) => {
    await openScreen(page, PRODUCTS, { viewport, theme: "light" });
    const sent = [];
    await page.route(/\/admin\/products$/, (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const b = route.request().postDataJSON();
      sent.push(b);
      const variants = [];
      for (const s of b.options?.[0]?.values || []) for (const c of b.options?.[1]?.values || []) variants.push({ id: `v-${s}-${c}`, option_values: { Size: s, Colour: c } });
      return route.fulfill(json({ id: "p-new", name: b.name, price_cents: b.price_cents, variants }));
    });

    await page.getByRole("button", { name: "Add product" }).first().click();
    await page.getByLabel("Product name").fill("Linen shirt");
    await page.getByLabel("Price (R)", { exact: true }).fill("450");
    await page.getByLabel("It comes in sizes or colours").check();
    await page.getByLabel("Size values (comma separated)").fill("S, M, s");
    await page.getByLabel("Colour values (comma separated)").fill("Red, Blue");
    await expect(page.getByRole("status").filter({ hasText: "This makes 4 versions: S / Red, S / Blue, M / Red, M / Blue." })).toBeVisible();
    await page.getByText("SKU, barcode and cost price").click();
    await page.getByLabel("SKU (your own code)").fill("SHIRT");
    await page.getByLabel("Cost price (R)").fill("180.50");
    await page.getByLabel("Stock of each version (optional)").fill("3");

    expect(await serious(page, ".vpr-block")).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

    await page.locator(".vpr-form").first().getByRole("button", { name: "Add product" }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]).toMatchObject({
      name: "Linen shirt", price_cents: 45000, sku: "SHIRT", cost_cents: 18050, stock_quantity: 3,
      options: [{ name: "Size", values: ["S", "M"] }, { name: "Colour", values: ["Red", "Blue"] }],
    });
    await expect(page.getByText("Linen shirt added, in 4 versions.")).toBeVisible();
  });
}

test("a price that isn't an amount is refused before anything is sent", async ({ page }) => {
  await openScreen(page, PRODUCTS, { viewport: VIEWPORTS.phone ?? Object.values(VIEWPORTS)[0], theme: "light" });
  let posted = 0;
  await page.route(/\/admin\/products$/, (route) => { if (route.request().method() === "POST") posted++; return route.fallback(); });
  await page.getByRole("button", { name: "Add product" }).first().click();
  await page.getByLabel("Product name").fill("Mug");
  await page.getByLabel("Price (R)", { exact: true }).fill("ninety");
  await page.locator(".vpr-form").first().getByRole("button", { name: "Add product" }).click();
  await expect(page.getByText(`"ninety" isn't a price.`, { exact: false })).toBeVisible();
  expect(posted).toBe(0);
  await expect(page.getByLabel("Product name")).toHaveValue("Mug");             // nothing cleared
});

test("the editor makes the versions from the options and sets a photo per colour", async ({ page }) => {
  await openScreen(page, PRODUCTS, { viewport: Object.values(VIEWPORTS).at(-1), theme: "light" });
  let variants = [];
  const generated = [], photos = [];
  await page.route(/\/admin\/products\/p-02\/variants$/, (route) => route.request().method() === "GET"
    ? route.fulfill(json({ variants })) : route.fallback());
  await page.route(/\/admin\/products\/p-02\/variants\/generate$/, (route) => {
    const b = route.request().postDataJSON(); generated.push(b);
    variants = b.options[0].values.map((c, i) => ({ id: `v${i}`, option_values: { Colour: c }, sort_order: i, archived: false }));
    return route.fulfill(json({ created: variants.length, kept: 0, variants, not_in_options: [] }));
  });
  await page.route(/\/admin\/products\/p-02\/variants\/photo$/, (route) => {
    const b = route.request().postDataJSON(); photos.push(b);
    variants = variants.map((v) => (v.option_values.Colour === b.value ? { ...v, image_url: b.image_url } : v));
    return route.fulfill(json({ updated: 1, image_url: b.image_url }));
  });
  await page.route(/\/admin\/products$/, (route) => route.request().method() === "GET"
    ? route.fulfill(json({ products: [{ id: "p-02", name: "Sourdough loaf", category: "extras", price_cents: 6500, in_stock: true,
        images: ["https://img.vula.test/loaf-white.jpg", "https://img.vula.test/loaf-rye.jpg"], options: null }] }))
    : route.fallback());
  // the list loaded before these routes: open another Sell tab and come back so it loads through them
  await page.getByRole("tab", { name: /Reviews$/ }).first().click();
  await page.getByRole("tab", { name: /Products$/ }).first().click();
  await page.getByRole("button", { name: "Edit Sourdough loaf" }).first().waitFor();
  await page.getByRole("button", { name: "Edit Sourdough loaf" }).first().click();
  await page.getByLabel("Option 1 name").fill("Colour");
  await page.getByLabel("Colour values (comma separated)").fill("White, Rye");
  await page.getByRole("button", { name: "Make the versions" }).click();
  await expect.poll(() => generated.length).toBe(1);
  expect(generated[0]).toEqual({ options: [{ name: "Colour", values: ["White", "Rye"] }] });
  await expect(page.getByText("2 new versions made.")).toBeVisible();

  await page.getByRole("radio", { name: "Photo 2 for Rye" }).click();
  await expect.poll(() => photos.length).toBe(1);
  expect(photos[0]).toEqual({ option: "Colour", value: "Rye", image_url: "https://img.vula.test/loaf-rye.jpg" });
  await expect(page.getByRole("radio", { name: "Photo 2 for Rye" })).toHaveAttribute("aria-checked", "true");
  expect(await serious(page, ".vpp")).toEqual([]);
});
