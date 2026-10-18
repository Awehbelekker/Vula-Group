// command.spec.js — the redesign's Home, Today and All activity (UX redesign Phase E). UI_V2=1.
// New screens, so the axe check GATES (serious/critical = fail), and no sideways scroll at 390px.
// What each role is shown is the server's filtering (mocked per role in mockApi); this checks the
// screens show exactly that and never offer a Back house to a login without Money.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

test.skip(process.env.UI_V2 !== "1", "the redesign runs with UI_V2=1");
const out = (ti, name) => path.join(ti.project.testDir, ".results", "command", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

const SCREENS = [
  { name: "home", nav: null, ready: "Won in" },
  { name: "today", nav: "Today", ready: "Platter tasting" },
  { name: "activity", nav: "All activity", ready: "Chat with Fatima Adams" },
];

for (const s of SCREENS) {
  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`${s.name} owner ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, { ...s, role: "owner" }, { viewport, theme });
        await page.screenshot({ path: out(ti, `${s.name}-${vp}-${theme}.png`), fullPage: true, animations: "disabled" });
        expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
        if (vp === "mobile") expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
        expect(errors).toEqual([]);
      });
    }
  }
}

test("home: front, back and all; every figure opens its list", async ({ page }) => {
  await openScreen(page, { name: "home", role: "owner", nav: null, ready: "Won in" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByRole("heading", { level: 1, name: /Good (morning|afternoon|evening), Demo/ })).toBeVisible();
  await expect(page.getByText("14 days left in October · 4 open orders · 3 chats waiting on you")).toBeVisible();
  await page.getByRole("tab", { name: "Back house" }).click();
  await expect(page.getByText("Invoices overdue")).toBeVisible();
  await page.getByRole("button", { name: /Invoices overdue/ }).click();
  await expect(page.getByRole("tab", { name: /Invoices$/, selected: true })).toBeVisible();
});

test("home: a staff login without Money gets no Back house", async ({ page }) => {
  await openScreen(page, { name: "home", role: "staff", nav: null, ready: "Chats waiting on you" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByRole("tab", { name: "Front house" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Back house" })).toHaveCount(0);
});

test("activity: filters and the detail panel", async ({ page }) => {
  await openScreen(page, { name: "activity", role: "owner", nav: "All activity", ready: "Chat with Fatima Adams" }, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.locator(".vui-chip", { hasText: "Back house" }).click();
  await expect(page.getByText("Chat with Fatima Adams")).toHaveCount(0);
  await expect(page.getByText("Invoice INV-2026-0121")).toBeVisible();
  await page.getByText("Invoice INV-2026-0121").click();
  const sheet = page.getByRole("dialog", { name: "Invoice INV-2026-0121" });
  await expect(sheet.getByText("R 12 500")).toBeVisible();
  await expect(page.locator(".vui-toolbar")).toBeVisible();
  expect(bad(await new AxeBuilder({ page }).include(".vui-sheet").analyze())).toEqual([]);
  await sheet.getByRole("button", { name: "Open the list" }).click();
  await expect(page.getByRole("tab", { name: /Invoices$/, selected: true })).toBeVisible();
});

test("today is on the phone toolbar", async ({ page }) => {
  await openScreen(page, { name: "home", role: "owner", nav: null, ready: "Won in" }, { viewport: VIEWPORTS.mobile, theme: "light" });
  await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "Today" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Saturday,? 17 October/ })).toBeVisible();
  await expect(page.getByText("Quote QUO-2026-0031 drafted by Vula — approve")).toBeVisible();
});
