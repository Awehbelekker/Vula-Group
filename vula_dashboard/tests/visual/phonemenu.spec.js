// phonemenu.spec.js — the brand kit's three phone-menu looks (migration 251, 8 Nov 2026; from
// Ian's Glass Nav Kit): Classic, Liquid glass and Floating button.
//
//   node_modules/.bin/playwright test phonemenu
//
// Each look, at 360 / 390 / 430 px, light and dark, in Vula green and in DIGG's near-black (the
// colour that made four of the kit's six looks disappear on a dark bar):
//   - every button fits on the screen and is a full tap target;
//   - the current page and the Add button stand out from the bar (contrast 3:1 or more);
//   - a page's last row isn't hidden under the bar, raised Add button included;
//   - Liquid glass: the pill moves to the page you tap; Floating button: Add sits in the notch.
// Then Settings › Brand kit: picking a look previews it, changes the real menu straight away and
// saves `nav_style`. Screenshots go to .results-phonemenu/.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { installMockApi, seedSession } from "./mockApi.js";
import * as F from "./fixtures/demo.js";

const WIDTHS = { 360: 780, 390: 844, 430: 932 };
const LOOKS = ["classic", "glass", "float"];
const BRANDS = { vula: "#2C5545", digg: "#1E1E1E" };
const outDir = (ti) => path.join(ti.project.testDir, ".results-phonemenu");

test.describe.configure({ mode: "parallel" });

async function boot(page, { look, accent, theme, width }) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  await page.setViewportSize({ width, height: WIDTHS[width] });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(F.NOW_ISO));
  await installMockApi(page, { role: "owner" });
  // registered after the mock table, so this answer wins
  await page.route(/\/v1\/commerce\/[^/]+\/brand$/, (r) => r.fulfill({
    status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" },
    body: JSON.stringify({ ...F.brand, accent_color: accent, nav_style: look }) }));
  await seedSession(page, { role: "owner", theme });
  await page.goto("/");
  await page.locator(".v2-shell").first().waitFor();
  await expect(page.locator(`.vui-toolbar[data-nav="${look}"]`)).toBeVisible();
  await page.waitForLoadState("networkidle", { timeout: 1500 }).catch(() => {});
  await page.waitForTimeout(300);
  return errors;
}

/** WCAG contrast of two computed CSS colours (rgb/rgba strings; alpha laid over `under`). */
function contrastIn(page, fg, bg) {
  return page.evaluate(([fgSel, bgSel]) => {
    const parse = (c) => (c.match(/[\d.]+/g) || []).map(Number);
    const solid = (el, prop) => {
      // walk up until the colour is opaque, blending as we go (the glass bar is see-through)
      let [r, g, b, a = 1] = parse(getComputedStyle(el)[prop]);
      let node = el.parentElement;
      while (a < 0.99 && node) {
        const [r2, g2, b2, a2 = 1] = parse(getComputedStyle(node).backgroundColor);
        if (a2 > 0) { r = r * a + r2 * (1 - a); g = g * a + g2 * (1 - a); b = b * a + b2 * (1 - a); a = a + a2 * (1 - a); }
        node = node.parentElement;
      }
      return [r, g, b];
    };
    const lum = ([r, g, b]) => {
      const c = [r, g, b].map((x) => { x /= 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    };
    const [fEl, fProp] = fgSel, [bEl, bProp] = bgSel;
    const L1 = lum(solid(document.querySelector(fEl), fProp)), L2 = lum(solid(document.querySelector(bEl), bProp));
    return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
  }, [fg, bg]);
}

for (const look of LOOKS) {
  for (const [brandName, accent] of Object.entries(BRANDS)) {
    for (const theme of ["light", "dark"]) {
      test(`${look} · ${brandName} · ${theme}: fits, stands out, hides nothing at 360/390/430`, async ({ page }, ti) => {
        for (const width of Object.keys(WIDTHS).map(Number)) {
          const errors = await boot(page, { look, accent, theme, width });
          const bar = page.getByRole("navigation", { name: "Quick actions" });
          const buttons = bar.getByRole("button");
          const n = await buttons.count();
          expect(n).toBeGreaterThanOrEqual(4);
          for (let i = 0; i < n; i++) {
            const box = await buttons.nth(i).boundingBox();
            expect(box.x, `button ${i} off the left edge at ${width}`).toBeGreaterThanOrEqual(0);
            expect(box.x + box.width, `button ${i} off the right edge at ${width}`).toBeLessThanOrEqual(width);
            expect(box.y + box.height).toBeLessThanOrEqual(WIDTHS[width]);
            expect(Math.min(box.width, box.height), `button ${i} too small to tap`).toBeGreaterThanOrEqual(44);
          }
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

          // the Add button stands out from the bar (and from the page, where it rises above it)
          const add = bar.getByRole("button", { name: "Add" });
          if (look === "float") {
            expect(await contrastIn(page, [".vui-tool--primary", "backgroundColor"], [".vui-toolbar-bg", "backgroundColor"]),
              "Add vs the bar").toBeGreaterThanOrEqual(3);
            const [a, b] = [await add.boundingBox(), await bar.boundingBox()];
            expect(a.y, "Add rises above the bar").toBeLessThan(b.y);
            expect(await bar.locator(".vui-toolbar-line path").getAttribute("d")).toMatch(/^M[\d.]+,[\d.]+ L/);
          }
          // the current page stands out: full-strength text, marked in the brand (glass pill, float dot)
          await bar.getByRole("button", { name: /^Inbox/ }).click();
          await expect(bar.getByRole("button", { name: /^Inbox/ })).toHaveAttribute("aria-current", "page");
          if (look !== "classic") {
            const bgSel = look === "glass" ? ".v2-shell" : ".vui-toolbar-bg";
            expect(await contrastIn(page, [".vui-tool[aria-current='page']", "color"], [bgSel, "backgroundColor"]),
              "current page vs the bar").toBeGreaterThanOrEqual(3);
          }
          if (look === "float") {
            expect(await contrastIn(page, [".vui-tool[aria-current='page']", "color"], [".vui-toolbar-bg", "backgroundColor"]))
              .toBeGreaterThanOrEqual(4.5);
            expect(await bar.getByRole("button", { name: /^Inbox/ }).evaluate((b) => getComputedStyle(b, "::after").backgroundColor))
              .not.toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
          }
          if (look === "glass") {
            const pill = await bar.locator(".vui-toolbar-ind").boundingBox();
            const inbox = await bar.getByRole("button", { name: /^Inbox/ }).boundingBox();
            expect(Math.abs((pill.x + pill.width / 2) - (inbox.x + inbox.width / 2)), "pill under Inbox").toBeLessThan(2);
          }

          // the page's last row clears the bar, the raised button included: scrolled to the end,
          // the space kept free under the content is at least as tall as the bar stands
          await page.mouse.move(width / 2, 200);   // scroll to the end the way a thumb would
          for (let i = 0, y = -1; i < 12; i++) {
            await page.mouse.wheel(0, 1500);
            await page.waitForTimeout(120);
            const now = await page.evaluate(() => window.scrollY);
            if (now === y) break;
            y = now;
          }
          const short = await page.evaluate(() => {
            const pad = document.querySelector(".v2-content .vui-toolbar-pad");
            const bar = document.querySelector(".vui-toolbar");
            const top = Math.min(...[bar, ...bar.querySelectorAll(".vui-tool")].map((e) => e.getBoundingClientRect().top));
            const content = [...pad.parentElement.children].filter((e) => e !== pad).map((e) => e.getBoundingClientRect().bottom);
            return Math.round(Math.max(...content) - top);
          });
          expect(short, `the page's last row is ${short}px under the bar at ${width}`).toBeLessThanOrEqual(0);

          fs.mkdirSync(outDir(ti), { recursive: true });
          await page.screenshot({ path: path.join(outDir(ti), `${look}-${brandName}-${theme}-${width}.png`) });
          expect(errors).toEqual([]);
        }
      });
    }
  }
}

test("brand kit: picking a phone menu previews it, changes the menu live and saves nav_style", async ({ page }, ti) => {
  const errors = await boot(page, { look: "classic", accent: BRANDS.vula, theme: "light", width: 390 });
  await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "More" }).click();
  const sheet = page.getByRole("dialog", { name: "Menu" });
  await sheet.locator(".vui-row", { has: page.locator(".vui-row-title", { hasText: /^Settings$/ }) }).first().click();
  await page.locator(".vst-nav").getByRole("button", { name: "Brand kit" }).click();
  const group = page.getByRole("group", { name: "Phone menu" });
  await expect(group.getByRole("button", { name: "Classic" })).toHaveAttribute("aria-pressed", "true");

  await group.getByRole("button", { name: "Floating button" }).click();
  const preview = page.getByRole("navigation", { name: "Phone menu preview" });
  await expect(preview).toHaveAttribute("data-nav", "float");
  await expect(page.getByRole("navigation", { name: "Quick actions" })).toHaveAttribute("data-nav", "float");  // live
  await group.getByRole("button", { name: "Liquid glass" }).click();
  await expect(preview).toHaveAttribute("data-nav", "glass");
  await preview.getByRole("button", { name: /^Inbox/ }).click();
  await expect(preview.getByRole("button", { name: /^Inbox/ })).toHaveAttribute("aria-current", "page");
  await preview.scrollIntoViewIfNeeded();
  fs.mkdirSync(outDir(ti), { recursive: true });
  await page.screenshot({ path: path.join(outDir(ti), "brand-kit-preview.png") });

  const saved = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname.endsWith("/admin/invoice-settings"));
  await page.getByRole("button", { name: "Save brand kit" }).click();
  expect((await saved).postDataJSON()).toMatchObject({ nav_style: "glass", accent_color: "#2C5545" });
  expect(errors).toEqual([]);
});
