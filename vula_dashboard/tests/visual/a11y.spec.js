// a11y.spec.js — accessibility BASELINE (reports, never fails on existing violations).
//
//   npx playwright test a11y
//
// 1. axe-core on each of the 8 screens at desktop / light → baseline/axe-baseline.json
//    ({ screen: [{ id, impact, nodes }] } plus per-impact totals).
// 2. At 390px (mobile, light): does the page scroll sideways (scrollWidth > 390)?
//    → baseline/hscroll-baseline.json
// The file runs serially (one worker, in order) so the last test can merge the part files
// each check writes; ~1–2 minutes in total.
import { test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs";
import path from "node:path";
import { SCREENS, VIEWPORTS, openScreen } from "./screens.js";

const partsDir = (ti) => path.join(ti.project.testDir, ".results", "a11y-parts");
const baseDir = (ti) => path.join(ti.project.testDir, "baseline");
const writePart = (ti, name, data) => {
  fs.mkdirSync(partsDir(ti), { recursive: true });
  fs.writeFileSync(path.join(partsDir(ti), name), JSON.stringify(data));
};

test.describe.configure({ mode: "serial" });
test.setTimeout(90000);

test.describe("a11y baseline", () => {
  for (const screen of SCREENS) {
    test(`axe ${screen.name} desktop light`, async ({ page }, ti) => {
      await openScreen(page, screen, { viewport: VIEWPORTS.desktop, theme: "light" });
      const res = await new AxeBuilder({ page }).analyze();
      const violations = res.violations
        .map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help }))
        .sort((a, b) => a.id.localeCompare(b.id));
      writePart(ti, `axe-${screen.name}.json`, violations);
    });

    test(`hscroll ${screen.name} mobile`, async ({ page }, ti) => {
      await openScreen(page, screen, { viewport: VIEWPORTS.mobile, theme: "light" });
      const m = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      writePart(ti, `hscroll-${screen.name}.json`, { ...m, hscroll: m.scrollWidth > 390 });
    });
  }
});

// Runs last (serial mode) and merges the parts.
test("a11y baseline: write summary", async ({}, ti) => {
  const read = (f) => { try { return JSON.parse(fs.readFileSync(path.join(partsDir(ti), f), "utf8")); } catch { return null; } };
  const axe = {}, hscroll = {};
  for (const s of SCREENS) {
    const v = read(`axe-${s.name}.json`);
    if (v) axe[s.name] = { by_impact: v.reduce((acc, x) => ({ ...acc, [x.impact]: (acc[x.impact] || 0) + 1 }), {}), violations: v };
    const h = read(`hscroll-${s.name}.json`);
    if (h) hscroll[s.name] = h;
  }
  fs.mkdirSync(baseDir(ti), { recursive: true });
  fs.writeFileSync(path.join(baseDir(ti), "axe-baseline.json"),
    JSON.stringify({ viewport: "desktop 1440x900", theme: "light", screens: axe }, null, 2) + "\n");
  fs.writeFileSync(path.join(baseDir(ti), "hscroll-baseline.json"),
    JSON.stringify({ viewport: "mobile 390x844", screens: hscroll }, null, 2) + "\n");
});
