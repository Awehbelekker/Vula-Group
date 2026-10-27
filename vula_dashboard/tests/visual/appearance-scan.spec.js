// appearance-scan.spec.js — Settings › Appearance and the camera button (2026-10-27, Ian: "Where is
// the theme settings?" … "we should have a camera/scanner button"). The redesign (UI_V2=1).
import { test, expect } from "@playwright/test";
import { VIEWPORTS, openScreen } from "./screens.js";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

test.skip(process.env.UI_V2 !== "1", "the redesign runs with UI_V2=1");
const SETTINGS = { name: "settings", role: "owner", nav: "Settings", ready: "Brand kit" };
// a 1×1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");

test("Settings › Appearance: light, dark or match the device — and the header button follows", async ({ page }) => {
  await openScreen(page, SETTINGS, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.locator(".vst-nav").getByRole("button", { name: "Appearance" }).click();
  const looks = page.getByRole("radiogroup", { name: "Appearance" });
  await expect(looks.getByRole("radio")).toHaveCount(3);
  await expect(looks.getByRole("radio", { name: /^Light/ })).toHaveAttribute("aria-checked", "true");   // the session's choice

  await looks.getByRole("radio", { name: /^Dark/ }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => localStorage.getItem("vula-theme-mode"))).toBe("dark");
  await expect(page.getByRole("button", { name: /Theme: dark/ })).toBeVisible();          // the header agrees

  await page.getByRole("button", { name: /Theme: dark/ }).click();                         // and the other way round
  await expect(looks.getByRole("radio", { name: /Match this device/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);

  await page.getByRole("button", { name: "Your business’s colours, logo and fonts" }).click();
  await expect(page.locator("#vst-title")).toHaveText("Brand kit");
});

async function home(page, modules = []) {
  await page.setViewportSize(VIEWPORTS.mobile);
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner", modules });
  await seedSession(page, { role: "owner", theme: "light" });
  await page.goto("/");
  await page.locator(".v2-shell").first().waitFor();
}

test("the camera button takes a photo straight into the Smart Scanner", async ({ page }) => {
  await home(page, ["scanner"]);
  const scans = [];
  page.on("request", (r) => { if (/\/admin\/scan$/.test(new URL(r.url()).pathname)) scans.push(r.postDataJSON()); });
  await expect(page.getByRole("button", { name: "Scan a document with the camera" })).toBeVisible();
  // the button opens the phone's camera (a file input with capture); the test hands it the photo
  await page.getByTestId("scan-camera-input").setInputFiles({ name: "till slip.png", mimeType: "image/png", buffer: PNG });
  await expect(page.getByRole("dialog", { name: "Smart scan" })).toBeVisible();
  await expect.poll(() => scans.length).toBe(1);
  expect(scans[0].image_base64.startsWith("data:image/png;base64,")).toBe(true);           // read, nothing booked yet
});

test("no camera button for a login that can't use the scanner", async ({ page }) => {
  await home(page);
  await expect(page.getByRole("button", { name: "Scan a document with the camera" })).toHaveCount(0);
});
