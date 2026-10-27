// install.spec.js — "Install Vula" (2026-10-27, PWA pass 2): where the browser offers its own
// install pop-up the button opens it; on an iPhone (no pop-up exists) the Share → Add to Home Screen
// steps show; the phone's Home card can be waved away for a month; nothing shows once installed.
// The Home card is the redesign's (UI_V2=1); the menu item is in both designs.
import { test, expect, devices } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";
const ANDROID = devices["Pixel 7"].userAgent;
const IPHONE = devices["iPhone 14"].userAgent;

async function open(page, { width = 390, standalone = false } = {}) {
  if (standalone) await page.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true }));
  await page.setViewportSize({ width, height: 844 });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner" });
  await seedSession(page, { role: "owner", theme: "light" });
  await page.goto("/");
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
}

// What Chrome does once the page is installable: a cancelable event carrying its own pop-up.
const offerInstall = (page, outcome = "accepted") => page.evaluate((o) => {
  const e = new Event("beforeinstallprompt", { cancelable: true });
  e.prompt = () => { window.__installPrompted = (window.__installPrompted || 0) + 1; return Promise.resolve(); };
  e.userChoice = Promise.resolve({ outcome: o });
  window.dispatchEvent(e);
}, outcome);

const card = (page) => page.getByRole("region", { name: "Install Vula" });

async function menuInstall(page) {
  if (V2) {
    await page.getByRole("button", { name: "More" }).click();
    return page.getByRole("dialog", { name: "Menu" }).getByRole("button", { name: /Install Vula/ });
  }
  return page.getByRole("button", { name: "Install Vula" });          // the old design's top bar
}

test.describe("on an Android phone", () => {
  test.use({ userAgent: ANDROID });

  test("Install Vula opens the browser's own install pop-up", async ({ page }) => {
    test.skip(!V2, "the Home card is the redesign's");
    await open(page);
    await offerInstall(page);
    await expect(card(page)).toBeVisible();
    await card(page).getByRole("button", { name: "Install Vula" }).click();
    await expect.poll(() => page.evaluate(() => window.__installPrompted)).toBe(1);
    await expect(page.getByRole("dialog", { name: "Put Vula on your home screen" })).toHaveCount(0);
    await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));
    await expect(card(page)).toHaveCount(0);                                  // installed: the card goes
    await expect(page.getByText("Vula is on your home screen")).toBeVisible();
  });

  test("with no pop-up from the browser, the menu shows the steps", async ({ page }) => {
    await open(page);
    await (await menuInstall(page)).click();
    const sheet = page.getByRole("dialog", { name: "Put Vula on your home screen" });
    await expect(sheet.getByRole("list", { name: "How to install Vula" })).toContainText("Install app");
    await sheet.getByRole("button", { name: "Done" }).click();
    await expect(sheet).toHaveCount(0);
  });

  test("Not now hides the Home card for a month, on this phone", async ({ page }) => {
    test.skip(!V2, "the Home card is the redesign's");
    await open(page);
    await card(page).getByRole("button", { name: "Not now" }).click();
    await expect(card(page)).toHaveCount(0);
    await page.reload();
    await page.locator(".v2-shell").first().waitFor();
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await expect(card(page)).toHaveCount(0);
    expect(await page.evaluate(() => Number(localStorage.getItem("vula.install.dismissed")) > 0)).toBe(true);
  });
});

test.describe("on an iPhone", () => {
  test.use({ userAgent: IPHONE });

  test("Install Vula shows Safari's Share → Add to Home Screen steps", async ({ page }) => {
    await open(page);
    if (V2) await card(page).getByRole("button", { name: "Install Vula" }).click();
    else await (await menuInstall(page)).click();
    const steps = page.getByRole("dialog", { name: "Put Vula on your home screen" }).getByRole("list", { name: "How to install Vula" });
    await expect(steps.getByRole("listitem")).toHaveCount(3);
    await expect(steps).toContainText("Share");
    await expect(steps).toContainText("Add to Home Screen");
  });

  test("once Vula is on the home screen, nothing asks to install it", async ({ page }) => {
    await open(page, { standalone: true });
    await expect(card(page)).toHaveCount(0);
    const item = await menuInstall(page);
    await expect(page.getByRole("dialog", { name: "Menu" }).or(page.locator(".vshell")).first()).toBeVisible();
    await expect(item).toHaveCount(0);
  });
});

test("a computer gets no Home card, and the menu offers the install", async ({ page }) => {
  await open(page, { width: 1280 });
  await expect(card(page)).toHaveCount(0);
  if (V2) {
    await page.getByRole("button", { name: /^Account:/ }).click();
    await page.getByRole("menuitem", { name: "Install Vula" }).click();
  } else {
    await page.getByRole("button", { name: "Install Vula" }).click();
  }
  await expect(page.getByRole("dialog", { name: "Put Vula on your home screen" })).toContainText("Chrome or Edge");
});
