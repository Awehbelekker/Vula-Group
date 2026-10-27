// pwa.spec.js — Vula as an app on the phone (2026-10-27, PWA pass 1): signing out leaves nothing of
// that person on a shared phone, an offline phone says so, the manifest lets tablets turn, and the
// service worker updates with every build and opens offline. Either design (UI_V2=1 for the
// redesign). Service workers are blocked in the other specs; the "service worker" block allows it.
import { test, expect } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";

async function open(page, { viewport = { width: 1280, height: 860 } } = {}) {
  await page.setViewportSize(viewport);
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner" });
  await seedSession(page, { role: "owner", theme: "light" });
  await page.goto("/");
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
}

async function signOut(page) {
  const account = page.getByRole("button", { name: /^Account:/ });
  if (await account.isVisible()) {
    await account.click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
  } else {
    await page.getByRole("button", { name: "Sign out" }).first().click();
  }
}

const stored = (page) => page.evaluate(async () => ({
  keys: Object.keys(localStorage).sort(),
  share: await caches.has("vula-share"),
  user: (JSON.parse(localStorage.getItem("vula-auth") || "{}").state || {}).user || null,
}));

test("signing out leaves nothing of that person on the phone, and keeps the phone's own settings", async ({ page }) => {
  await open(page);
  await page.evaluate(async () => {
    localStorage.setItem("vula.masterTenant", "harbour-vine");
    localStorage.setItem("vula.ui.tenant.harbour-vine", "v2");
    localStorage.setItem("vula-theme-mode", "dark");
    localStorage.setItem("vp.vol", "0.4");
    (await caches.open("vula-share")).put("/shared/0", new Response("%PDF-1.4"));
  });
  await signOut(page);
  await expect.poll(async () => (await stored(page)).user).toBeNull();
  const after = await stored(page);
  expect(after.share).toBe(false);                                         // shared files gone
  expect(after.keys).not.toContain("vula.masterTenant");
  expect(after.keys).not.toContain("vula.ui.tenant.harbour-vine");
  expect(after.keys).toEqual(expect.arrayContaining(["vula-theme-mode", "vp.vol"]));   // the phone's own
});

test("unsent stock scans are not lost without asking", async ({ page }) => {
  await open(page);
  await page.evaluate(() => localStorage.setItem("vula.stockq.harbour-vine.c1", JSON.stringify([{ id: 1 }, { id: 2 }])));
  await signOut(page);
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("2 stock scans haven’t been sent yet");
  await dialog.getByRole("button", { name: "Stay signed in" }).click();
  expect((await stored(page)).user).not.toBeNull();                       // still signed in
  expect((await stored(page)).keys).toContain("vula.stockq.harbour-vine.c1");

  await signOut(page);
  await page.getByRole("dialog").getByRole("button", { name: "Sign out anyway" }).click();
  await expect.poll(async () => (await stored(page)).user).toBeNull();
  expect((await stored(page)).keys).not.toContain("vula.stockq.harbour-vine.c1");
});

test("an offline phone says so, and stops saying so when it's back", async ({ page, context }) => {
  await open(page, { viewport: { width: 390, height: 844 } });
  const banner = page.getByRole("status").filter({ hasText: "You’re offline" });
  await expect(banner).toHaveCount(0);
  await context.setOffline(true);
  await expect(banner).toBeVisible();
  await context.setOffline(false);
  await expect(banner).toHaveCount(0);
});

test("an iPhone opens the app on the business's own launch screen, light or dark", async ({ page }) => {
  await open(page);
  const links = page.locator('link[rel="apple-touch-startup-image"]');
  await expect(links).toHaveCount(24);                                  // 12 iPhones × light and dark
  const pro = await page.locator('link[rel="apple-touch-startup-image"][media*="device-width: 393px"][media*="dark"]').getAttribute("href");
  expect(pro).toMatch(/\/v1\/commerce\/demo-shop\/app-splash\/1179x2556\.png\?dark=true$/);
});

test("the manifest installs Vula and lets a tablet turn sideways", async ({ request }) => {
  const m = await (await request.get("/manifest.webmanifest")).json();
  expect(m.display).toBe("standalone");
  expect(m.id).toBe("/");
  expect(m.orientation).toBeUndefined();
  const sizes = m.icons.filter((i) => i.type === "image/png").map((i) => `${i.sizes}:${i.purpose}`);
  expect(sizes).toEqual(expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable"]));
  expect(m.share_target?.action).toBe("/share-target");
  const offline = await request.get("/offline.html");
  expect(offline.ok()).toBe(true);
  expect(await offline.text()).toContain("You’re offline");
});

test("the app's icon shows how many customers are waiting, and clears on sign-out", async ({ page }) => {
  await page.addInitScript(() => {
    window.__badge = [];
    navigator.setAppBadge = async (n) => { window.__badge.push(n); };
    navigator.clearAppBadge = async () => { window.__badge.push(0); };
  });
  await open(page);
  await expect.poll(() => page.evaluate(() => window.__badge.at(-1))).toBeGreaterThan(0);
  const waiting = await page.evaluate(() => window.__badge.at(-1));
  await signOut(page);
  await expect.poll(() => page.evaluate(() => window.__badge?.at(-1))).toBe(0);
  expect(waiting).toBeGreaterThan(0);
});

test.describe("service worker", () => {
  test.use({ serviceWorkers: "allow" });

  async function controlled(page) {
    await open(page, { viewport: { width: 390, height: 844 } });
    await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, null, { timeout: 15000 });
  }

  test("is registered for this build and takes over the page", async ({ page }) => {
    await controlled(page);
    const url = await page.evaluate(async () => (await navigator.serviceWorker.ready).active.scriptURL);
    expect(url).toMatch(/\/sw\.js\?v=[\w-]+$/);
    expect(await page.evaluate(() => caches.keys())).toEqual(expect.arrayContaining([expect.stringMatching(/^vula-shell-/)]));
  });

  // Going offline can't be emulated for a service worker's own requests in Chromium, so what it
  // does offline is unit-tested (tests/unit/sw.test.mjs); here: it keeps what that needs.
  test("keeps the offline page and the app shell for when there's no signal", async ({ page }) => {
    await controlled(page);
    await page.reload();
    await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
    await expect.poll(() => page.evaluate(async () => Boolean(await caches.match("/offline.html")))).toBe(true);
    await expect.poll(() => page.evaluate(async () => Boolean(await caches.match("/")))).toBe(true);
  });

  // The dev server has no build, so this build's file list is handed to the worker here.
  test("keeps every screen's files when it installs, not only the screens opened so far", async ({ page, context }) => {
    await context.route(/\/precache\.json/, (route) => route.fulfill({ contentType: "application/json",
      body: JSON.stringify({ build: "test", files: ["/assets/NeverOpened-x1.js", "/assets/NeverOpened-x1.css"] }) }));
    await context.route(/\/assets\/NeverOpened-x1\.(js|css)$/, (route) => route.fulfill({
      contentType: route.request().url().endsWith(".js") ? "text/javascript" : "text/css", body: "/* a screen */" }));
    await controlled(page);
    await expect.poll(() => page.evaluate(async () => Boolean(await caches.match("/assets/NeverOpened-x1.js")))).toBe(true);
    expect(await page.evaluate(async () => Boolean(await caches.match("/assets/NeverOpened-x1.css")))).toBe(true);
  });

  test("a customer's receipt page is never kept on the phone", async ({ page }) => {
    await controlled(page);
    await page.goto("/r/test-token").catch(() => {});
    expect(await page.evaluate(async () => Boolean(await caches.match("/r/test-token")))).toBe(false);
  });
});
