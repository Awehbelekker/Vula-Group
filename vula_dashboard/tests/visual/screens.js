// screens.js — shared navigation for the visual baseline + a11y specs.
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

export const VIEWPORTS = {
  mobile: { width: 390, height: 844 },
  tablet: { width: 834, height: 1112 },
  desktop: { width: 1440, height: 900 },
};
export const THEMES = ["light", "dark"];

// nav = sidebar item label (.vshell-it), tab = SectionTabs sub-tab label (role="tab").
// ready = text that proves the seeded data rendered (not a login / blank / "Loading…").
export const SCREENS = [
  // the redesign's Home (UX redesign Phase E) replaces the go-live checklist page
  { name: "home", role: "owner", nav: null, ready: process.env.UI_V2 === "1" ? "Won in" : "Getting you live" },
  { name: "inbox", role: "owner", nav: "Inbox", ready: "Waiting on you" },
  { name: "customers", role: "owner", nav: "People", tab: "Customers", ready: "Thandi Mokoena" },
  { name: "orders", role: "owner", nav: "Sell", tab: "Orders", ready: "HV-1052" },
  { name: "invoices", role: "owner", nav: "Money", tab: "Invoices", ready: "INV-2026-0118" },
  { name: "documents", role: "owner", nav: "Operate", tab: "Documents", ready: "Atlantic-Dairy-invoice-BILL-0031.pdf" },
  { name: "settings", role: "owner", nav: "Settings", ready: "Brand kit" },
  { name: "master", role: "master", nav: "Master", ready: "need attention ·" },
];

const HIDE_CARET = `*, *::before, *::after { caret-color: transparent !important; }`;

/** The redesigned shell: sidebar on desktop, icon rail (aria-label) on tablet, More sheet on a phone. */
async function openV2(page, label) {
  const exact = new RegExp(`^${label}$`);
  if (await page.locator(".v2-shell--desktop").count()) {
    await page.locator(".v2-nav-item", { has: page.locator(".v2-nav-lb", { hasText: exact }) }).first().click();
  } else if (await page.locator(".v2-shell--tablet").count()) {
    await page.locator(`.v2-side.rail .v2-nav-item[aria-label="${label}"]`).first().click();
  } else {
    await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "Menu" });
    await sheet.locator(".vui-row", { has: page.locator(".vui-row-title", { hasText: exact }) }).first().click();
    await sheet.waitFor({ state: "hidden" });
  }
}

/** Boot the app as `screen.role`, navigate to the screen and wait until it has settled.
 * Returns the list of page errors seen (the caller asserts it is empty). */
export async function openScreen(page, screen, { viewport, theme }) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}\n${(e.stack || "").split("\n").slice(1, 4).join("\n")}`));
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(NOW_ISO));     // Date.now()/new Date() frozen; timers still run
  await installMockApi(page, { role: screen.role });
  await seedSession(page, { role: screen.role, theme });

  await page.goto("/");
  // a UI_V2 run waits for the redesigned shell (the tenant's flag arrives after first paint)
  await page.locator(process.env.UI_V2 === "1" && screen.role !== "master" ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();

  if (screen.nav && await page.locator(".v2-shell").count()) {
    await openV2(page, screen.nav);
  } else if (screen.nav) {
    const burger = page.locator("button.vshell-burger");
    if (await burger.isVisible()) {           // < 860px: the sidebar is a drawer
      await burger.click();
      await page.locator(".vshell-side.open").waitFor();
    }
    await page.locator(".vshell-it", { has: page.locator(".vshell-lb", { hasText: new RegExp(`^${screen.nav}$`) }) }).first().click();
  }
  if (screen.tab) {
    await page.getByRole("tab", { name: new RegExp(`${screen.tab}$`) }).first().click();
  }

  await page.getByText(screen.ready, { exact: false }).first().waitFor({ timeout: 15000 });
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: HIDE_CARET });
  // Let lazy effects (GlassCard ResizeObserver, brand apply) finish a frame or two.
  await page.waitForTimeout(400);
  return errors;
}
