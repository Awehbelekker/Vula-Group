// scanq.spec.js — Smart Scanner photos with no signal (2026-10-27, PWA pass 4): they wait on the
// phone (Cache Storage "vula-scanq", src/lib/scanQueue.js), are read when it's back online — still
// checked before anything is booked — and signing out asks before deleting them. Either design
// (UI_V2=1 for the redesign), on a phone.
import { test, expect } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

async function open(page, width = 390) {
  await page.setViewportSize({ width, height: 844 });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner", modules: ["scanner", "expenses", "documents"] });
  await seedSession(page, { role: "owner", theme: "light" });
  await page.goto("/");
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
  const scans = [];
  page.on("request", (r) => { if (/\/admin\/scan$/.test(new URL(r.url()).pathname)) scans.push(r.postDataJSON()); });
  return scans;
}

// A photo handed to the scanner the way the phone's Share sheet does (share/ShareSheet.jsx).
async function photoIntoScanner(page) {
  await page.evaluate(async (png) => {
    const body = Uint8Array.from(atob(png), (c) => c.charCodeAt(0));
    await (await caches.open("vula-share")).put("/shared/0", new Response(new Blob([body], { type: "image/png" }),
      { headers: { "Content-Type": "image/png", "X-Name": "till slip.png" } }));
    window.location.hash = "#/share";
  }, PNG);
  await page.getByRole("dialog", { name: "Shared with Vula" }).getByRole("button", { name: /Scan it/ }).click();
}

const waitingOnPhone = (page) => page.evaluate(async () => (await (await caches.open("vula-scanq")).keys()).length);

test("a photo taken with no signal waits on the phone, and is read once it's back online", async ({ page, context }) => {
  const scans = await open(page);
  await photoIntoScanner(page);                                                // the scanner open, online
  await expect.poll(() => scans.length).toBe(1);
  await page.getByRole("button", { name: /Retake/ }).click();

  await context.setOffline(true);                                              // the signal goes
  await page.locator("#vsc-file, #scan-input").setInputFiles({ name: "slip.png", mimeType: "image/png", buffer: Buffer.from(PNG, "base64") });
  await expect(page.getByRole("status").filter({ hasText: "No signal — the photo is saved on this phone" })).toBeVisible();
  await expect(page.getByLabel("Waiting on this phone")).toContainText("1 photo waiting on this phone");
  expect(await waitingOnPhone(page)).toBe(1);
  expect(scans.length).toBe(1);                                                // nothing tried while offline

  await context.setOffline(false);
  await expect.poll(() => scans.length).toBe(2);                               // read by itself when back
  expect(scans[1].image_base64.startsWith("data:image/png;base64,")).toBe(true);
  await expect(page.getByText("Atlantic Dairy Co-op").first()).toBeVisible();  // what Vula read, to check
  await expect(page.getByLabel("Waiting on this phone")).toHaveCount(0);
  expect(await waitingOnPhone(page)).toBe(0);
  expect(scans.length).toBe(2);                                                // read once, nothing booked
});

test("signing out with photos still waiting asks first", async ({ page }) => {
  await open(page, 1280);
  await page.evaluate(async () => {
    await (await caches.open("vula-scanq")).put("/scan-queue/1-abc", new Response(new Blob(["x"], { type: "image/png" }),
      { headers: { "Content-Type": "image/png", "X-Doc-Type": "auto" } }));
  });
  const account = page.getByRole("button", { name: /^Account:/ });
  if (await account.isVisible()) {
    await account.click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
  } else {
    await page.getByRole("button", { name: "Sign out" }).first().click();
  }
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("1 scanner photo hasn’t been sent yet");
  await dialog.getByRole("button", { name: "Stay signed in" }).click();
  expect(await waitingOnPhone(page)).toBe(1);                                  // kept
});
