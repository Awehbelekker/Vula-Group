// mobile.spec.js — every screen on a phone, both designs (mobile pass, 24 Oct 2026).
//
//   node_modules/.bin/playwright test mobile            (the old design)
//   UI_V2=1 node_modules/.bin/playwright test mobile    (the redesign)
//
// Tenants run their business from a phone, beside WhatsApp. This crawls the whole menu — every
// menu item and every tab under it — as an owner (every module on), a sales rep and Master, at
// 360 (most Androids), 390 and 430 px, and measures each screen:
//   hscroll  the page scrolls sideways
//   wide     an element runs past the screen edge (outside a scroller)
//   edge     text closer than 12px to the screen edge
//   small    a button, link or field smaller than 32px either way
//   hidden   the page's last control sits under the floating toolbar / home bar after scrolling
//   gutter   the side margins on a phone are wider than 24px (space the screen can't spare)
// Findings go to .results/mobile/<design>-<role>-<width>.json with a screenshot per screen, and
// the test FAILS on hscroll, wide, edge and hidden (small is reported only).
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";
const DESIGN = V2 ? "v2" : "v1";
const WIDTHS = [360, 390, 430];
const HEIGHT = { 360: 780, 390: 844, 430: 932 };
const MODULES = ["projects", "fieldops", "workspace", "estimating", "ai_draft", "followups", "bookings",
  "stock", "suppliers", "costing", "expenses", "scanner", "flows", "email"];
const ROLES = [
  { role: "owner", modules: MODULES },
  { role: "rep", modules: [] },
  { role: "master", modules: [] },
];
// beside .results, not in it: Playwright empties .results at the start of every run
const outDir = (ti) => path.join(ti.project.testDir, ".results-mobile");
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

test.describe.configure({ mode: "parallel" });
test.setTimeout(15 * 60 * 1000);

async function boot(page, { role, modules }, width) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  page.setDefaultTimeout(8000);           // a control that never appears is a finding, not a hang
  page.setDefaultNavigationTimeout(60000); // the dev server compiles on the first load
  // a browser alert/confirm freezes the page (and every measurement): note it and close it
  page.on("dialog", (d) => { errors.push(`unexpected ${d.type()} dialog: ${d.message().slice(0, 80)}`); d.dismiss().catch(() => {}); });
  await page.setViewportSize({ width, height: HEIGHT[width] });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role, modules });
  await seedSession(page, { role, theme: "light" });
  await page.goto("/");
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
  await settle(page);
  return errors;
}

async function settle(page) {
  // the app polls (inbox, today), so "network idle" may never come: give it a moment instead
  await page.waitForLoadState("networkidle", { timeout: 1500 }).catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
}

/** The menu's items (and Master's zones), as the phone shows them. */
async function menu(page) {
  if (V2) {
    await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "Menu" });
    await sheet.waitFor();
    const zones = await sheet.getByRole("group", { name: "Workspace" }).getByRole("button").allInnerTexts().catch(() => []);
    const items = {};
    for (const z of zones.length ? zones : [null]) {
      if (z) await sheet.getByRole("group", { name: "Workspace" }).getByRole("button", { name: z }).click();
      items[z || ""] = (await sheet.locator(".vui-row-title").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
    }
    await page.keyboard.press("Escape");
    await sheet.waitFor({ state: "hidden" });
    return items;
  }
  const burger = page.locator("button.vshell-burger");
  if (await burger.isVisible()) { await burger.click(); await page.locator(".vshell-side.open").waitFor(); }
  const zones = (await page.locator(".vshell-zone-btn").allInnerTexts()).map((t) => t.trim());
  const items = {};
  for (const z of zones.length ? zones : [null]) {
    if (z) await page.locator(".vshell-zone-btn", { hasText: z }).first().click();
    items[z || ""] = (await page.locator(".vshell-it .vshell-lb").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  }
  if (await page.locator(".vshell-side.open").count()) await burger.click();
  return items;
}

async function go(page, zone, label) {
  const exact = new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
  if (V2) {
    await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "Menu" });
    if (zone) await sheet.getByRole("group", { name: "Workspace" }).getByRole("button", { name: zone }).click();
    await sheet.locator(".vui-row", { has: page.locator(".vui-row-title", { hasText: exact }) }).first().click();
    await sheet.waitFor({ state: "hidden" }).catch(() => page.keyboard.press("Escape"));
  } else {
    const burger = page.locator("button.vshell-burger");
    if (await burger.isVisible() && !(await page.locator(".vshell-side.open").count())) {
      await burger.click(); await page.locator(".vshell-side.open").waitFor();
    }
    if (zone) await page.locator(".vshell-zone-btn", { hasText: zone }).first().click();
    await page.locator(".vshell-it", { has: page.locator(".vshell-lb", { hasText: exact }) }).first().click();
  }
  await settle(page);
}

/** Measured in the page: see the header for what each finding means. */
function measure() {
  const W = window.innerWidth, H = window.innerHeight;
  const root = document.querySelector(".v2-content") || document.querySelector(".vshell-main") || document.querySelector("main") || document.body;
  const shown = (el) => {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) !== 0;
  };
  const clipped = (el) => {
    for (let p = el; p && p !== document.documentElement; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return true;
    }
    return false;
  };
  const where = (el) => {
    const parts = [];
    for (let e = el, i = 0; e && e !== document.body && i < 4; e = e.parentElement, i++) {
      const cls = typeof e.className === "string" ? e.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".") : "";
      parts.unshift(e.tagName.toLowerCase() + (cls ? `.${cls}` : ""));
    }
    return parts.join(" > ");
  };
  const seen = new Set(), once = (k) => !seen.has(k) && seen.add(k);
  const wide = [], edge = [], small = [];
  for (const el of root.querySelectorAll("*")) {
    if (!shown(el) || getComputedStyle(el).position === "fixed") continue;
    const r = el.getBoundingClientRect();
    if ((r.right > W + 1 || r.left < -1) && !clipped(el) && once(`w${where(el)}`))
      wide.push({ at: where(el), left: Math.round(r.left), right: Math.round(r.right) });
  }
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const t = walk.currentNode, el = t.parentElement;
    if (!t.textContent.trim() || !el || !shown(el) || clipped(el)) continue;
    if (el.getBoundingClientRect().width <= 2) continue;                  // screen-reader-only text
    const range = document.createRange(); range.selectNodeContents(t);
    const r = [...range.getClientRects()].find((x) => x.width > 0);
    if (r && (r.left < 12 || r.right > W - 12) && once(`e${where(el)}`))
      edge.push({ at: where(el), text: t.textContent.trim().slice(0, 40), left: Math.round(r.left), right: Math.round(W - r.right) });
  }
  const controls = [...root.querySelectorAll("button, a[href], [role=button], [role=tab], input:not([type=hidden]), select, textarea, [role=checkbox], [role=switch]")].filter(shown);
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    if (el.tagName === "A" && getComputedStyle(el).display === "inline") continue;     // a link inside a sentence
    if (el.matches("input[type=checkbox], input[type=radio], input[type=range]")) continue;   // label / thumb is the target
    if ((r.height < 32 || r.width < 32) && once(`s${where(el)}`))
      small.push({ at: where(el), label: (el.getAttribute("aria-label") || el.textContent || el.getAttribute("name") || "").trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height) });
  }
  // the page's side gutters: where its blocks (cards, sections, fields) start and end
  let gl = W, gr = W;
  for (const el of root.querySelectorAll("*")) {
    if (!shown(el) || getComputedStyle(el).position === "fixed") continue;
    const r = el.getBoundingClientRect();
    if (r.width < W * 0.5 || r.width >= W - 1 || r.left < 0 || r.right > W) continue;
    let scrolled = false;
    for (let p = el.parentElement; p && p !== root; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === "auto" || ox === "scroll") { scrolled = true; break; }
    }
    if (scrolled) continue;
    gl = Math.min(gl, r.left); gr = Math.min(gr, W - r.right);
  }
  // after scrolling to the end, nothing the user can tap may sit under a bottom overlay
  window.scrollTo(0, document.documentElement.scrollHeight);
  const overlays = [...document.querySelectorAll("body *")].filter((el) => {
    const cs = getComputedStyle(el);
    if (cs.position !== "fixed" || !shown(el) || root.contains(el)) return false;
    const r = el.getBoundingClientRect();
    return r.bottom >= H - 40 && r.top > H / 2 && r.height < H / 2;
  }).map((el) => ({ el, r: el.getBoundingClientRect() }));
  const inBox = (el) => {        // inside its own scroll area (a list, a table): out of view there, not under the toolbar
    for (let p = el.parentElement; p && p !== root; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (/auto|scroll/.test(cs.overflowY) || /auto|scroll/.test(cs.overflowX)) return true;
    }
    return false;
  };
  const hidden = [];
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    if (r.bottom <= 0 || r.top >= H || inBox(el)) continue;
    const o = overlays.find(({ r: q }) => r.bottom > q.top + 2 && r.right > q.left && r.left < q.right);
    if (o && once(`h${where(el)}`)) hidden.push({ at: where(el), label: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 30), under: where(o.el) });
  }
  window.scrollTo(0, 0);
  return { hscroll: Math.max(0, document.documentElement.scrollWidth - W), gutter: [Math.round(gl), Math.round(gr)], wide, edge, small, hidden };
}

for (const who of ROLES) {
  for (const width of WIDTHS) {
    test(`${DESIGN} ${who.role} ${width}px`, async ({ page }, ti) => {
      const errors = await boot(page, who, width);
      const dir = outDir(ti);
      fs.mkdirSync(path.join(dir, "shots"), { recursive: true });
      const report = [];
      let t0 = Date.now();
      const check = async (name) => {
        const t1 = Date.now();
        const m = await Promise.race([page.evaluate(measure),
          new Promise((_, no) => setTimeout(() => no(new Error("measuring took over 20s")), 20000))]);
        const t2 = Date.now();
        await page.screenshot({ path: path.join(dir, "shots", `${DESIGN}-${who.role}-${width}-${slug(name)}.png`), fullPage: true });
        report.push({ screen: name, ms: { open: t1 - t0, measure: t2 - t1, shot: Date.now() - t2 }, ...m });
        t0 = Date.now();
      };
      await check("home");
      const items = await menu(page);
      for (const [zone, labels] of Object.entries(items)) {
        for (const label of labels) {
          if (/^(sign out|log out|install vula)$/i.test(label)) continue;   // actions, not screens (install.spec.js covers the install sheet)
          try { await go(page, zone || null, label); } catch (e) { report.push({ screen: `${zone} ${label}`, error: String(e).slice(0, 200) }); continue; }
          const name = `${zone ? `${zone} › ` : ""}${label}`;
          try { await check(name); } catch (e) { report.push({ screen: name, error: String(e).slice(0, 200) }); continue; }
          const tabs = page.locator(V2 ? ".v2-content [role=tablist]" : ".vshell-main [role=tablist]").first().getByRole("tab");
          const names = (await tabs.allInnerTexts()).map((t) => t.trim()).filter(Boolean);
          for (const tab of names.slice(1)) {
            try {
              await tabs.filter({ hasText: tab }).first().click({ timeout: 4000 });
              await settle(page);
              await check(`${name} › ${tab}`);
            } catch (e) { report.push({ screen: `${name} › ${tab}`, error: String(e).slice(0, 200) }); }
          }
        }
      }
      fs.writeFileSync(path.join(dir, `${DESIGN}-${who.role}-${width}.json`), JSON.stringify(report, null, 2));
      const roomy = (r) => r.gutter && (r.gutter[0] > 24 || r.gutter[1] > 24) && r.gutter[0] < width / 2;
      const bad = report.filter((r) => r.hscroll || r.wide?.length || r.edge?.length || r.hidden?.length || roomy(r))
        .map((r) => `${r.screen}: ${[r.hscroll && `scrolls sideways ${r.hscroll}px`, r.wide?.length && `${r.wide.length} too wide`,
          r.edge?.length && `${r.edge.length} at the edge`, r.hidden?.length && `${r.hidden.length} under the toolbar`,
          roomy(r) && `gutters ${r.gutter.join("/")}px`].filter(Boolean).join(", ")}`);
      expect.soft(errors).toEqual([]);
      expect(bad).toEqual([]);
    });
  }
}
