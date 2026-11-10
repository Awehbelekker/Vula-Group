// session.spec.js — when a phone's sign-in runs out (8 Nov 2026).
//
//   node_modules/.bin/playwright test session
//
// Off the Hook's phone kept showing the dashboard after its sign-in had run out: every request
// was turned away (401), screens quietly stayed empty, and connecting a mailbox said "check the
// password" when the mail server was never even asked. Now:
//   - a 401 with no Supabase session left → the login page, saying so, with the email filled in,
//     keeping the phone's unsent stock scans for the same person;
//   - a 401 while Supabase still vouches for the session → nothing happens (never a false sign-out);
//   - connecting a mailbox shows the real reason it failed.
import { test, expect } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import * as F from "./fixtures/demo.js";

const OWNER = "owner@harbourvine.example.test";
const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*" };
const json = (body, status = 200) => ({ status, contentType: "application/json", headers: cors, body: JSON.stringify(body) });

async function boot(page, { session = false } = {}) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(`${e.name}: ${e.message}`));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.clock.setFixedTime(new Date(F.NOW_ISO));
  await installMockApi(page, { role: "owner" });
  await seedSession(page, { role: "owner" });
  await page.addInitScript(() => { try { localStorage.setItem("vula.stockq.count-1", JSON.stringify([{ sku: "A1", qty: 3 }])); } catch { /* */ } });
  if (session) {
    // a live Supabase session that Supabase itself still vouches for
    await page.addInitScript((email) => {
      const exp = Math.floor(Date.now() / 1000) + 3600 * 24 * 365;
      localStorage.setItem("sb-supabase-auth-token", JSON.stringify({ access_token: "a.b.c", refresh_token: "r", token_type: "bearer",
        expires_in: 3600 * 24 * 365, expires_at: exp, user: { id: "u-owner", email, aud: "authenticated" } }));
    }, OWNER);
    await page.route(/supabase\.vula\.test\/auth\/v1\/user/, (r) => r.fulfill(json({ id: "u-owner", email: OWNER, aud: "authenticated" })));
  }
  return errors;
}

test("a sign-in that ran out goes back to the login page, says so, and keeps unsent scans", async ({ page }) => {
  const errors = await boot(page);
  // Vula turns the Home figures away: sign in required
  await page.route(/\/v1\/commerce\/[^/]+\/admin\/home$/, (r) => r.fulfill(json({ detail: "Sign in required." }, 401)));
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "You've been signed out on this phone" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Email", { exact: true })).toHaveValue(OWNER);
  // the same person signing back in still has their count
  expect(await page.evaluate(() => localStorage.getItem("vula.stockq.count-1"))).toContain("A1");
  expect(errors).toEqual([]);
});

test("a 401 while the sign-in is still good signs nobody out", async ({ page }) => {
  const errors = await boot(page, { session: true });
  await page.route(/\/v1\/commerce\/[^/]+\/admin\/home$/, (r) => r.fulfill(json({ detail: "Sign in required." }, 401)));
  const checked = page.waitForRequest(/supabase\.vula\.test\/auth\/v1\/user/);
  await page.goto("/");
  await checked;
  await page.waitForTimeout(500);
  await expect(page.locator(".v2-shell")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a refresh token Supabase refuses (400) goes back to the login page: DIGG's phone, 10 Nov 2026", async ({ page }) => {
  // The phone's access token had expired; renewing it got 400 "Invalid Refresh Token", and every
  // Vula request after that was a 401. That 400 used to read as "couldn't tell", so the app stayed
  // put and Invoices loaded for ever.
  const errors = await boot(page);
  await page.addInitScript((email) => {
    const past = Math.floor(Date.now() / 1000) - 3600;
    localStorage.setItem("sb-supabase-auth-token", JSON.stringify({ access_token: "a.b.c", refresh_token: "gone",
      token_type: "bearer", expires_in: 3600, expires_at: past, user: { id: "u-owner", email, aud: "authenticated" } }));
  }, OWNER);
  await page.route(/supabase\.vula\.test\/auth\/v1\/token/, (r) => r.fulfill(json(
    { code: "refresh_token_not_found", error_code: "refresh_token_not_found", msg: "Invalid Refresh Token: Refresh Token Not Found" }, 400)));
  await page.route(/\/v1\/(commerce|team|documents)\/[^/]+\/(admin\/)?[^?]*/, (r) => r.fulfill(json({ detail: "Sign in required." }, 401)));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("status").filter({ hasText: "You've been signed out on this phone" })).toBeVisible();
  await expect(page.getByLabel("Email", { exact: true })).toHaveValue(OWNER);
  expect(errors).toEqual([]);
});

async function openEmailSettings(page) {
  await page.getByRole("navigation", { name: "Quick actions" }).getByRole("button", { name: "More" }).click();
  const sheet = page.getByRole("dialog", { name: "Menu" });
  await sheet.locator(".vui-row", { has: page.locator(".vui-row-title", { hasText: /^Settings$/ }) }).first().click();
  await page.locator(".vst-nav").getByRole("button", { name: "Email (IMAP / SMTP)" }).click();
  await page.getByRole("button", { name: "Connect a mailbox" }).click();
  await page.getByLabel("Email address").fill("info@harbourvine.example.test");
  await page.getByLabel("Password / app password").fill("not-a-real-password");
  await page.getByLabel("IMAP host").fill("mail.harbourvine.example.test");
}

test("connecting a mailbox says why it failed", async ({ page }) => {
  const errors = await boot(page, { session: true });
  await page.route(/\/v1\/email\/connect$/, (r) => (r.request().method() === "POST"
    ? r.fulfill(json({ status: "error", error: "The mail server refused the password.", detail: "b'[AUTHENTICATIONFAILED]'" }))
    : r.fulfill({ status: 204, headers: cors })));
  await page.goto("/");
  await page.locator(".v2-shell").first().waitFor();
  await openEmailSettings(page);
  await page.getByRole("button", { name: "Connect mailbox" }).click();
  await expect(page.getByRole("alert")).toHaveText("Couldn't connect: The mail server refused the password.");
  expect(errors).toEqual([]);
});

test("connecting a mailbox while signed out says so, then goes to the login page", async ({ page }) => {
  const errors = await boot(page);
  await page.route(/\/v1\/email\/connect$/, (r) => (r.request().method() === "POST"
    ? r.fulfill(json({ detail: "Sign in required." }, 401)) : r.fulfill({ status: 204, headers: cors })));
  await page.goto("/");
  await page.locator(".v2-shell").first().waitFor();
  await openEmailSettings(page);
  await page.getByRole("button", { name: "Connect mailbox" }).click();
  // never "check the password": the login page, saying why
  await expect(page.getByRole("status").filter({ hasText: "You've been signed out on this phone" })).toBeVisible({ timeout: 10000 });
  expect(errors).toEqual([]);
});
