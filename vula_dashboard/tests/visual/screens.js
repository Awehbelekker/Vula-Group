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
  { name: "home", role: "owner", nav: null, ready: "Getting you live" },
  { name: "inbox", role: "owner", nav: "Inbox", ready: "Waiting on you" },
  { name: "customers", role: "owner", nav: "People", tab: "Customers", ready: "Thandi Mokoena" },
  { name: "orders", role: "owner", nav: "Sell", tab: "Orders", ready: "HV-1052" },
  { name: "invoices", role: "owner", nav: "Money", tab: "Invoices", ready: "INV-2026-0118" },
  { name: "documents", role: "owner", nav: "Operate", tab: "Documents", ready: "Atlantic-Dairy-invoice-BILL-0031.pdf" },
  { name: "settings", role: "owner", nav: "Settings", ready: "Brand kit" },
  { name: "master", role: "master", nav: "Master", ready: "need attention ·" },
];

const HIDE_CARET = `*, *::before, *::after { caret-color: transparent !important; }`;

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
  await page.locator(".vshell").waitFor();

  if (screen.nav) {
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
