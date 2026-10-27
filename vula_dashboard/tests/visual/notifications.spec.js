// notifications.spec.js — notifications on this phone (2026-10-27, PWA pass 3). The redesign
// (UI_V2=1). A headless browser has no push service, so the browser's push subscription is stood in
// for (window.__push); everything the app sends the API is real.
import { test, expect, devices } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

test.skip(process.env.UI_V2 !== "1", "the redesign runs with UI_V2=1");

const KEY = { public_key: "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U",
  enabled: true, alerts: ["New paid orders", "A customer waiting for a person", "Approvals waiting for you"], has_whatsapp: true };

async function open(page, { key = KEY, subscribed = false, width = 390 } = {}) {
  await page.addInitScript((on) => {
    const sub = { endpoint: "https://push.example/this-phone", toJSON() { return { endpoint: this.endpoint, keys: { p256dh: "p", auth: "a" } }; },
      unsubscribe: async () => { window.__push.sub = null; return true; } };
    window.__push = { sub: on ? sub : null, asked: 0 };
    window.PushManager = window.PushManager || function PushManager() {};
    const pm = { getSubscription: async () => window.__push.sub, subscribe: async () => (window.__push.sub = sub) };
    const fakeReg = { pushManager: pm };
    if (navigator.serviceWorker) navigator.serviceWorker.getRegistration = async () => fakeReg;
    window.Notification = { permission: on ? "granted" : "default",
      requestPermission: async () => { window.__push.asked += 1; window.Notification.permission = "granted"; return "granted"; } };
  }, subscribed);
  await page.setViewportSize({ width, height: 844 });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner" });
  const calls = [];
  await page.route(/\/v1\/push\/[^/]+\/(key|subscribe|unsubscribe|test)$/, (route) => {
    const what = new URL(route.request().url()).pathname.split("/").pop();
    calls.push({ what, body: route.request().postDataJSON?.() ?? null });
    route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify(what === "key" ? key : what === "test" ? { sent: true } : { ok: true }) });
  });
  await seedSession(page, { role: "owner", theme: "light" });
  await page.goto("/");
  await page.locator(".v2-shell").first().waitFor();
  return calls;
}

async function fromMenu(page) {
  await page.getByRole("button", { name: "More" }).click();
  await page.getByRole("dialog", { name: "Menu" }).getByRole("button", { name: /Notifications/ }).click();
  return page.getByRole("dialog", { name: "Notifications" });
}

test("turn notifications on, test them and turn them off — from the phone's menu", async ({ page }) => {
  const calls = await open(page);
  const sheet = await fromMenu(page);
  await expect(sheet.getByRole("list", { name: "Alerts you get" }).getByRole("listitem")).toHaveCount(3);
  await sheet.getByRole("button", { name: "Turn on notifications" }).click();
  await expect(sheet.getByRole("status")).toHaveText("Notifications are on for this device.");
  expect(await page.evaluate(() => window.__push.asked)).toBe(1);                       // the browser asked first
  expect(calls.find((c) => c.what === "subscribe").body).toEqual({ endpoint: "https://push.example/this-phone", keys: { p256dh: "p", auth: "a" } });

  await sheet.getByRole("button", { name: "Send a test" }).click();
  await expect(sheet.getByRole("status")).toContainText("Sent");
  await sheet.getByRole("button", { name: "Turn off" }).click();
  await expect(sheet.getByRole("button", { name: "Turn on notifications" })).toBeVisible();
  expect(calls.find((c) => c.what === "unsubscribe").body).toEqual({ endpoint: "https://push.example/this-phone" });
});

test("signing out stops this phone getting that person's alerts", async ({ page }) => {
  const calls = await open(page, { subscribed: true });
  await page.getByRole("button", { name: "More" }).click();
  await page.getByRole("dialog", { name: "Menu" }).getByRole("button", { name: /Sign out/ }).click();
  await expect.poll(() => calls.some((c) => c.what === "unsubscribe")).toBe(true);
  expect(await page.evaluate(() => window.__push.sub)).toBeNull();
});

test("an owner finds it in Settings › On this device too", async ({ page }) => {
  await open(page, { width: 1280 });
  await page.getByRole("button", { name: "Settings" }).first().click();
  await page.locator(".vst-nav").getByRole("button", { name: "Notifications" }).click();
  await expect(page.locator(".vst-page").getByRole("button", { name: "Turn on notifications" })).toBeVisible();
});

test("nothing to turn on when notifications aren't available, and WhatsApp is still said to work", async ({ page }) => {
  await open(page, { key: { ...KEY, enabled: false, public_key: null } });
  const sheet = await fromMenu(page);
  await expect(sheet).toContainText("You still get every alert on WhatsApp");
  await expect(sheet.getByRole("button", { name: "Turn on notifications" })).toHaveCount(0);
});

test.describe("on an iPhone", () => {
  test.use({ userAgent: devices["iPhone 14"].userAgent });

  test("notifications need Vula on the home screen first", async ({ page }) => {
    await open(page);
    const sheet = await fromMenu(page);
    await expect(sheet).toContainText("once Vula is on your home screen");
    await sheet.getByRole("button", { name: "Install Vula" }).click();
    await expect(page.getByRole("dialog", { name: "Put Vula on your home screen" })).toBeVisible();
  });
});

test("an alert arriving while Vula is open chimes, and the chime can be switched off on this device", async ({ page }) => {
  await open(page, { subscribed: true });
  await page.locator(".v2-shell").click({ position: { x: 10, y: 200 } });               // the tap that lets a page make sound
  await page.evaluate(() => navigator.serviceWorker.dispatchEvent(new MessageEvent("message", { data: { type: "vula-alert", title: "New paid order" } })));
  await expect.poll(() => page.evaluate(() => window.__vulaChimes || 0)).toBe(1);
  const sheet = await fromMenu(page);
  const chime = sheet.getByRole("checkbox", { name: /Play a chime/ });
  await expect(chime).toBeChecked();
  await chime.uncheck();
  expect(await page.evaluate(() => localStorage.getItem("vula.alert.chime"))).toBe("off");
});
