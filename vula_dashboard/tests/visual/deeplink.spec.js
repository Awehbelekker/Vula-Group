// deeplink.spec.js — links from WhatsApp straight to one page: #/go/{page} (mobile pass, 24 Oct
// 2026). Vula's WhatsApp messages link to e.g. https://<dashboard>/#/go/invoices; the dashboard
// opens that page, keeps the link through a login, and never opens a page the login may not see.
// Runs in either design (UI_V2=1 for the redesign), on a phone.
import { test, expect } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";

async function boot(page, { role = "owner", url = "/", before } = {}) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role });
  await seedSession(page, { role, theme: "light" });
  if (before) await page.addInitScript(before);
  await page.goto(url);
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
}

test("a link to Invoices opens Invoices, and the address is tidied", async ({ page }) => {
  await boot(page, { url: "/#/go/invoices" });
  await expect(page.getByText("INV-2026-0118").locator("visible=true").first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.location.hash)).toBe("");
});

test("a link kept through the login opens once signed in", async ({ page }) => {
  // a Google or email-link sign-in replaces the address; the page was kept for this tab
  await boot(page, { before: () => { try { sessionStorage.setItem("vula.go", "invoices"); } catch { /* */ } } });
  await expect(page.getByText("INV-2026-0118").locator("visible=true").first()).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("vula.go"))).toBeNull();
});

test("a link while already signed in opens that page", async ({ page }) => {
  await boot(page);
  await page.evaluate(() => { window.location.hash = "#/go/invoices"; });
  await expect(page.getByText("INV-2026-0118").locator("visible=true").first()).toBeVisible();
});

test("a link to a page this login may not see does nothing", async ({ page }) => {
  await boot(page, { role: "staff", url: "/#/go/team" });
  await page.waitForTimeout(800);
  await expect(page.getByRole("heading", { name: "Team", exact: true })).toHaveCount(0);
});
