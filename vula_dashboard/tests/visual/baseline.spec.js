// baseline.spec.js — full-page screenshots of the 8 key screens × 3 viewports × 2 themes.
//
//   UPDATE_BASELINE=1 npx playwright test baseline   → writes tests/visual/baseline/*.png
//   npx playwright test baseline                     → same capture into tests/visual/.results/
//                                                      (no-op-safe: never touches the baseline)
// Later phases will switch this to toHaveScreenshot(); for now it just records the set.
// Every screen must load with zero `pageerror`s — the test fails otherwise.
import { test, expect } from "@playwright/test";
import path from "node:path";
import { SCREENS, VIEWPORTS, THEMES, openScreen } from "./screens.js";

// testDir (= tests/visual, from playwright.config.js) — import.meta isn't available here
// because Playwright loads these specs as CommonJS (package.json has no "type": "module").
const outDir = (testInfo) => process.env.UPDATE_BASELINE === "1"
  ? path.join(testInfo.project.testDir, "baseline")
  : path.join(testInfo.project.testDir, ".results", "baseline");

test.describe.configure({ mode: "parallel" });
test.setTimeout(60000);

for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    for (const screen of SCREENS) {
      test(`${screen.name} ${vpName} ${theme}`, async ({ page }, testInfo) => {
        const errors = await openScreen(page, screen, { viewport, theme });
        await page.screenshot({
          path: path.join(outDir(testInfo), `${screen.name}-${vpName}-${theme}.png`),
          fullPage: true, animations: "disabled", caret: "hide",
        });
        expect(errors, `page errors on ${screen.name}`).toEqual([]);
      });
    }
  }
}
