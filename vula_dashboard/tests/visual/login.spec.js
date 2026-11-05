// login.spec.js — the redesigned sign-in screen (login/LoginV2.jsx) and the Master "Open as
// tenant" banner (UX redesign Phase F, 2026-10-20). UI_V2=1. Each screen GATES on axe
// (serious/critical = fail) and on no sideways scroll at 390px.
import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";
import { installMockApi } from "./mockApi.js";
import * as F from "./fixtures/demo.js";
const { NOW_ISO } = F;

const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport, scope) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(scope).analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

// The QR exactly as the server makes it (wa_login._qr_svg): a fake one with a viewBox hid that the
// real one, fixed at 222px, was cut off in its 184px box (5 Nov 2026).
const QR_SVG = readFileSync(path.join(process.cwd(), "tests/visual/fixtures/wa-login-qr.svg"), "utf8");

/** Signed out, on the tenant's own address: the login wears its brand and offers a WhatsApp QR. */
async function openLogin(page, { viewport, theme }, calls = [], { tenant = F.TENANT_ID, status = "pending", brand = null } = {}) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner" });
  if (brand) {     // registered after the mock API, so it wins
    await page.route(/api\.vula\.test\/v1\/commerce\/[^/]+\/brand/, (route) => route.fulfill({
      status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(brand) }));
  }
  await page.addInitScript((t) => { try { localStorage.setItem("vula-theme-mode", t); } catch { /* blocked */ } }, theme);
  await page.route(/api\.vula\.test\/v1\/(tenants\/by-domain|auth\/wa\/)/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*" };
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    calls.push({ path: url.pathname, body: req.postDataJSON?.() ?? null, at: Date.now() });
    if (url.pathname.endsWith("/status") && typeof status === "function") {   // a scripted server
      const { code = 200, body = {}, delay = 0 } = status(calls);
      if (delay) await new Promise((r) => setTimeout(r, delay));
      return route.fulfill({ status: code, contentType: "application/json", headers: cors, body: JSON.stringify(body) }).catch(() => {});
    }
    const body = url.pathname.endsWith("/by-domain") ? (tenant ? { tenant_id: tenant } : {})
      : url.pathname.endsWith("/start") ? { available: true, id: "wal-1", secret: "s-1", link: "https://wa.me/27820000000?text=Login%20K7F4QX", qr_svg: QR_SVG, code: "K7F4QX" }
      : url.pathname.endsWith("/status") ? { status }
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

test("the WhatsApp QR fits its box: no edge cut off, so a phone can read it", async ({ page }) => {
  await openLogin(page, { viewport: VIEWPORTS.desktop, theme: "light" });
  const box = await page.getByRole("img", { name: "WhatsApp login QR code" }).boundingBox();
  const drawn = await page.locator(".vlg-qr svg path").first().boundingBox();     // the modules themselves
  expect(drawn.x).toBeGreaterThanOrEqual(box.x);
  expect(drawn.y).toBeGreaterThanOrEqual(box.y);
  expect(drawn.x + drawn.width).toBeLessThanOrEqual(box.x + box.width + 0.5);
  expect(drawn.y + drawn.height).toBeLessThanOrEqual(box.y + box.height + 0.5);
});

// Ian, 5 Nov 2026: after scanning, "Send me a code instead" did nothing. The QR's status check
// (one already in flight, or dev's second StrictMode start) answered "awaiting approval" after
// the switch and flipped the page back to the QR, so the code form never showed.
test("Send me a code instead works after the QR was scanned, and stops checking the QR", async ({ page }) => {
  const calls = [];
  await openLogin(page, { viewport: VIEWPORTS.desktop, theme: "light" }, calls, { status: "awaiting_approval" });
  await expect(page.getByText("Now tap Approve in WhatsApp on your phone…")).toBeVisible();
  await page.getByRole("button", { name: "Send me a code instead" }).click();
  await expect(page.getByLabel("Your WhatsApp number")).toBeVisible();
  const checks = calls.filter((c) => c.path.endsWith("/wa/status")).length;
  await page.waitForTimeout(5000);                      // more than two polls later
  await expect(page.getByLabel("Your WhatsApp number")).toBeVisible();
  await expect(page.getByRole("img", { name: "WhatsApp login QR code" })).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/wa/status")).length).toBeLessThanOrEqual(checks + 1);   // one may have been in flight
  // and back to the QR when they'd rather scan
  await page.getByRole("button", { name: "Scan a QR code instead" }).click();
  await expect(page.getByRole("img", { name: "WhatsApp login QR code" })).toBeVisible();
});

const DIGG_BRAND = { name: "DIGG Architecture", accent_color: "#000000", secondary_color: "#ed4b11", ink_color: "#1E1E1E", font_pairing: "modern" };

// Same report: on DIGG's black brand a tap showed nothing — the hover was a 4% darken, which does
// nothing to black or to a text-only button, and there was no pressed look at all.
test("buttons show hover and press on a black brand", async ({ page }) => {
  await openLogin(page, { viewport: VIEWPORTS.desktop, theme: "light" }, [], { brand: DIGG_BRAND });
  await expect(page.getByText("DIGG Architecture")).toBeVisible();
  const shadow = (b) => b.evaluate((el) => getComputedStyle(el).boxShadow);
  for (const name of ["Send me a code instead", "Sign in", "Continue with Google"]) {
    const b = page.getByRole("button", { name, exact: true });
    await page.mouse.move(0, 0);
    await page.waitForTimeout(200);
    const rest = await shadow(b);
    await b.hover();
    await page.waitForTimeout(200);
    const hover = await shadow(b);
    await page.mouse.down();
    await page.waitForTimeout(200);
    const pressed = await shadow(b);
    await page.mouse.up();
    expect(rest, name).toBe("none");
    expect(hover, name).toMatch(/inset/);
    expect(pressed, name).toMatch(/inset/);
    expect(pressed, name).not.toBe(hover);
  }
});

// Ian, 5 Nov 2026: "the button if you send code / login button". Inside the WhatsApp box a rule
// meant for the text links painted the filled buttons' labels in the brand colour, the colour of
// their own fill: on DIGG's black, "Send me a code" and "Log in" were blank black bars.
for (const theme of THEMES) {
  for (const [who, brand] of [["DIGG's black brand", DIGG_BRAND], ["Vula's own brand", null]]) {
    test(`WhatsApp code buttons have readable labels: ${who}, ${theme}`, async ({ page }) => {
      await openLogin(page, { viewport: VIEWPORTS.desktop, theme }, [], { brand });
      const ratio = (name) => page.getByRole("button", { name, exact: true }).evaluate((el) => {
        const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number);
        const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
          .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
        const cs = getComputedStyle(el);
        const [a, b] = [lum(rgb(cs.color)), lum(rgb(cs.backgroundColor))].sort((x, y) => y - x);
        return (a + 0.05) / (b + 0.05);
      });
      expect(await ratio("Sign in")).toBeGreaterThanOrEqual(3);
      await page.getByRole("button", { name: "Send me a code instead" }).click();
      await page.getByLabel("Your WhatsApp number").fill("082 000 0001");
      expect(await ratio("Send me a code")).toBeGreaterThanOrEqual(3);
      await page.getByRole("button", { name: "Send me a code", exact: true }).click();
      await page.getByLabel("6-digit code").fill("123456");
      expect(await ratio("Log in")).toBeGreaterThanOrEqual(3);
    });
  }
}

// Ian, 5 Nov 2026: a sign-in page sent hundreds of /start calls until the server answered 429.
// The QR was checked every 2s however long the server took, and any failed check (a 429, a
// database hiccup's 404) made a new QR — several at once when checks overlapped.
const starts = (calls) => calls.filter((c) => c.path.endsWith("/wa/start")).length;
const checks = (calls) => calls.filter((c) => c.path.endsWith("/wa/status"));

test("a busy server is waited out on the same QR: one check at a time, further apart, no new QR", async ({ page }) => {
  const calls = [];
  let open = 0, most = 0;
  await openLogin(page, { viewport: VIEWPORTS.desktop, theme: "light" }, calls, {
    status: () => { open += 1; most = Math.max(most, open); setTimeout(() => { open -= 1; }, 2500); return { code: 429, body: { detail: "Too many tries" }, delay: 2500 }; },
  });
  const before = starts(calls);
  await page.waitForTimeout(12000);
  expect(starts(calls)).toBe(before);                                  // no new QR
  expect(most).toBe(1);                                                // never two checks at once
  const at = checks(calls).map((c) => c.at);
  expect(at.length).toBeGreaterThanOrEqual(2);
  expect(at.length).toBeLessThanOrEqual(3);                            // 2s, then 4s, then 8s gaps (+2.5s answers)
  await expect(page.getByRole("img", { name: "WhatsApp login QR code" })).toBeVisible();
});

test("an expired QR is replaced at most three times, then the page offers a new one", async ({ page }) => {
  const calls = [];
  await openLogin(page, { viewport: VIEWPORTS.desktop, theme: "light" }, calls, { status: () => ({ body: { status: "expired" } }) });
  const first = starts(calls);
  await expect(page.getByRole("button", { name: "Show a new QR code" })).toBeVisible({ timeout: 20000 });
  expect(starts(calls) - first).toBe(3);
  await expect(page.getByRole("img", { name: "WhatsApp login QR code" })).toHaveCount(0);
  await expect(page.getByText("That code expired.")).toBeVisible();
  await page.waitForTimeout(3000);
  expect(starts(calls) - first).toBe(3);                               // and nothing more on its own
  await expect(page.getByRole("button", { name: "Send me a code instead" })).toBeVisible();
  await page.getByRole("button", { name: "Show a new QR code" }).click();
  await expect(page.getByRole("img", { name: "WhatsApp login QR code" })).toBeVisible();
  expect(starts(calls) - first).toBe(4);
});

test("a QR the server no longer knows (404) is replaced; a database hiccup (503) is not", async ({ page }) => {
  const calls = [];
  await openLogin(page, { viewport: VIEWPORTS.desktop, theme: "light" }, calls, {
    status: (cs) => (checks(cs).length === 1 ? { code: 503, body: {} } : checks(cs).length === 2 ? { code: 404, body: {} } : { body: { status: "pending" } }),
  });
  const first = starts(calls);
  await expect.poll(() => checks(calls).length, { timeout: 15000 }).toBeGreaterThanOrEqual(2);
  await expect.poll(() => starts(calls) - first, { timeout: 5000 }).toBe(1);
  await expect(page.getByText("That code expired — here is a fresh one.")).toBeVisible();
});

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
  // no code: the page says how to open the chat, and Resend asks for another to the same number
  await expect(page.getByText(/No code after a minute\? Send “hi” to your business's WhatsApp number/)).toBeVisible();
  await page.getByRole("button", { name: "Resend code" }).click();
  await expect(page.getByText("A new code is on its way.")).toBeVisible();
  const codeCalls = calls.filter((c) => c.path.endsWith("/wa/code"));
  expect(codeCalls).toHaveLength(2);
  expect(codeCalls[1].body).toEqual({ phone: "082 000 0001", host: "localhost" });
  await expect(page.getByLabel("6-digit code")).toHaveValue("");
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

// Ian, 5 Nov 2026: signed in as master on DIGG's address, Master HQ kept DIGG's tab title and
// icon. Master HQ wears Vula's; opening a tenant wears theirs; back to HQ is Vula's again.
test("Master HQ wears Vula's title and icon, a tenant opened as tenant wears theirs", async ({ page }) => {
  const head = () => page.evaluate(() => ({ title: document.title, icon: document.querySelector('link[rel="icon"]')?.getAttribute("href") }));
  await openScreen(page, MASTER, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.waitForLoadState("networkidle");
  expect(await head()).toEqual({ title: "Vula — Unlock Your Business", icon: "/icon.svg" });
  await page.getByText("Harbour & Vine Deli", { exact: true }).locator("visible=true").first().click();
  await page.getByRole("button", { name: "Open as tenant" }).click();
  const dlg = page.getByRole("dialog").filter({ hasText: "Why are you opening it?" });
  await dlg.getByRole("textbox").fill("support ticket #123");
  await dlg.getByRole("button", { name: "Open workspace" }).click();
  await page.locator(".v2-impersonate").waitFor();
  await expect.poll(async () => (await head()).title).not.toBe("Vula — Unlock Your Business");
  await page.getByRole("button", { name: "Master HQ" }).locator("visible=true").first().click();
  await expect.poll(head).toEqual({ title: "Vula — Unlock Your Business", icon: "/icon.svg" });
});
