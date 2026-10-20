// login.spec.js — the redesigned sign-in screen (login/LoginV2.jsx) and the Master "Open as
// tenant" banner (UX redesign Phase F, 2026-10-20). UI_V2=1. Each screen GATES on axe
// (serious/critical = fail) and on no sideways scroll at 390px.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";
import { installMockApi } from "./mockApi.js";
import * as F from "./fixtures/demo.js";
const { NOW_ISO } = F;

test.skip(process.env.UI_V2 !== "1", "the redesign runs with UI_V2=1");
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport, scope) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(scope).analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

const QR_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><rect width="21" height="21" fill="#fff"/><rect x="1" y="1" width="7" height="7"/><rect x="13" y="1" width="7" height="7"/><rect x="1" y="13" width="7" height="7"/><rect x="10" y="10" width="3" height="3"/></svg>';

/** Signed out, on the tenant's own address: the login wears its brand and offers a WhatsApp QR. */
async function openLogin(page, { viewport, theme }, calls = [], { tenant = F.TENANT_ID } = {}) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner" });
  await page.addInitScript((t) => { try { localStorage.setItem("vula-theme-mode", t); } catch { /* blocked */ } }, theme);
  await page.route(/api\.vula\.test\/v1\/(tenants\/by-domain|auth\/wa\/)/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*" };
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    calls.push({ path: url.pathname, body: req.postDataJSON?.() ?? null });
    const body = url.pathname.endsWith("/by-domain") ? (tenant ? { tenant_id: tenant } : {})
      : url.pathname.endsWith("/start") ? { available: true, id: "wal-1", secret: "s-1", link: "https://wa.me/27820000000?text=Login%20K7F4QX", qr_svg: QR_SVG, code: "K7F4QX" }
      : url.pathname.endsWith("/status") ? { status: "pending" }
      : url.pathname.endsWith("/code") ? { id: "wac-1", secret: "s-2", message: "We sent a 6-digit code to your WhatsApp." }
      : {};
    return route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(body) });
  });
  await page.goto("/");
  await page.getByRole("heading", { name: "Sign in" }).waitFor();
  await page.getByRole("img", { name: "WhatsApp login QR code" }).or(page.getByRole("link", { name: "Open WhatsApp" })).first().waitFor();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
  return errors;
}

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`login ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openLogin(page, { viewport, theme });
      await expect(page.locator(".vula-powered-by")).toContainText("Powered by");
      await expect(page.locator(".vula-powered-by").getByRole("img", { name: "Vula" })).toBeVisible();
      await check(page, ti, "login", `${vp}-${theme}`, viewport, "main.vlg");
      await page.getByRole("button", { name: "Create an account" }).click();
      await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
      await check(page, ti, "login-signup", `${vp}-${theme}`, viewport, "main.vlg");
      expect(errors).toEqual([]);
    });
  }
}

for (const theme of THEMES) {
  test(`login on Vula's own address ${theme}`, async ({ page }, ti) => {
    const errors = await openLogin(page, { viewport: VIEWPORTS.mobile, theme }, [], { tenant: null });
    await expect(page.locator(".vlg-brand").getByRole("img", { name: "Vula" })).toBeVisible();
    await expect(page.getByText("Business dashboard")).toBeVisible();
    await check(page, ti, "login-vula", `mobile-${theme}`, VIEWPORTS.mobile, "main.vlg");
    expect(errors).toEqual([]);
  });
}

test("login: the WhatsApp code path, reset and back make the same calls", async ({ page }) => {
  const calls = [];
  await openLogin(page, { viewport: VIEWPORTS.mobile, theme: "light" }, calls);
  // typing elsewhere on the page must not throw away the QR and start a new session
  // (dev's StrictMode mounts twice, so count what typing adds rather than an absolute number)
  const starts = () => calls.filter((c) => c.path.endsWith("/wa/start")).length;
  const before = starts();
  await page.getByLabel("Email").fill("owner@harbourvine.example.test");
  await page.waitForTimeout(300);
  expect(starts()).toBe(before);
  await page.getByRole("button", { name: "Send me a code instead" }).click();
  await page.getByLabel("Your WhatsApp number").fill("082 000 0001");
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByText("We sent a 6-digit code to your WhatsApp.")).toBeVisible();
  expect(calls.find((c) => c.path.endsWith("/wa/code")).body).toEqual({ phone: "082 000 0001", host: "localhost" });
  await expect(page.getByRole("button", { name: "Log in", exact: true })).toBeDisabled();
  await page.getByLabel("6-digit code").fill("12a34");
  await expect(page.getByLabel("6-digit code")).toHaveValue("1234");
  await page.getByLabel("6-digit code").fill("123456");
  await expect(page.getByRole("button", { name: "Log in", exact: true })).toBeEnabled();
  // the password can be shown to check for a typo, and hidden again
  const pw = page.getByRole("textbox", { name: "Password" }).or(page.locator("input[autocomplete=current-password]")).first();
  await page.locator("input[autocomplete=current-password]").fill("not-a-real-one");
  await expect(page.locator("input[autocomplete=current-password]")).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Show password" }).click();
  await expect(pw).toHaveAttribute("type", "text");
  await expect(page.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Hide password" }).click();
  await expect(page.locator("input[autocomplete=current-password]")).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Forgot password?" }).click();
  await expect(page.getByRole("heading", { name: "Reset password" })).toBeVisible();
  await expect(page.getByLabel("Email")).toHaveValue("owner@harbourvine.example.test");
  await page.getByRole("button", { name: "Back to sign in" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

const MASTER = { name: "master", role: "master", nav: "Master", ready: "need attention" };

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  for (const theme of THEMES) {
    test(`master open as tenant banner ${vp} ${theme}`, async ({ page }, ti) => {
      const errors = await openScreen(page, MASTER, { viewport, theme });
      await page.getByText("Harbour & Vine Deli", { exact: true }).locator("visible=true").first().click();
      await page.getByRole("button", { name: "Open as tenant" }).click();
      const dlg = page.getByRole("dialog").filter({ hasText: "Why are you opening it?" });
      await dlg.getByRole("textbox").fill("support ticket #123");
      await dlg.getByRole("button", { name: "Open workspace" }).click();
      const banner = page.locator(".v2-impersonate");
      await expect(banner).toContainText("Master mode: viewing");
      await expect(banner).toContainText("(support ticket #123)");
      await page.locator(".v2-shell").waitFor();
      await page.waitForLoadState("networkidle");
      await check(page, ti, "master-impersonate", `${vp}-${theme}`, viewport, ".v2-impersonate");
      await page.getByRole("button", { name: "Master HQ" }).locator("visible=true").first().click();
      await expect(banner).toHaveCount(0);
      await expect(page.getByText("need attention").locator("visible=true").first()).toBeVisible();
      expect(errors).toEqual([]);
    });
  }
}
