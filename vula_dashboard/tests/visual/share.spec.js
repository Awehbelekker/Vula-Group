// share.spec.js — sharing photos / PDFs into Vula from the phone's Share sheet (2026-10-24).
// public/sw.js keeps shared files in Cache Storage ("vula-share") and opens #/share; the sheet
// offers what this login may do with them. Here the cache is filled directly (the service worker
// isn't part of the dev server), in either design (UI_V2=1 for the redesign), on a phone.
import { test, expect } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";
const MODULES = ["scanner", "expenses", "documents"];
// a 1×1 PNG
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

async function share(page, files, { role = "owner" } = {}) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role, modules: MODULES });
  await seedSession(page, { role, theme: "light" });
  await page.goto("/");
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
  await page.evaluate(async ([list, png]) => {
    const cache = await caches.open("vula-share");
    await Promise.all(list.map(([name, type], i) => {
      const body = type === "image/png" ? Uint8Array.from(atob(png), (c) => c.charCodeAt(0)) : "%PDF-1.4 test";
      return cache.put(`/shared/${i}`, new Response(new Blob([body], { type }), { headers: { "Content-Type": type, "X-Name": encodeURIComponent(name) } }));
    }));
    window.location.hash = "#/share";
  }, [files, PNG]);
  return page.getByRole("dialog", { name: "Shared with Vula" });
}

test("a shared PDF waits in Documents for Upload", async ({ page }) => {
  const sheet = await share(page, [["Atlantic Dairy bill.pdf", "application/pdf"]]);
  await expect(sheet.getByText("Atlantic Dairy bill.pdf")).toBeVisible();
  await expect(sheet.getByRole("button", { name: /Scan it/ })).toHaveCount(0);        // a PDF isn't a photo to scan
  const ingests = [];
  page.on("request", (r) => { if (/\/ingest/.test(r.url())) ingests.push(r.url()); });
  await sheet.getByRole("button", { name: /Save to Documents/ }).click();
  await expect(sheet).toBeHidden();
  await expect(page.getByText("Atlantic Dairy bill.pdf").locator("visible=true").first()).toBeVisible();
  expect(ingests).toEqual([]);                                      // nothing uploaded until Upload is tapped
  expect(await page.evaluate(() => caches.has("vula-share"))).toBe(false);   // the device copy is gone
  expect(await page.evaluate(() => window.location.hash)).toBe("");
});

test("a shared photo can be scanned, and is read before anything is booked", async ({ page }) => {
  const scans = [];
  page.on("request", (r) => { if (/\/admin\/scan$/.test(new URL(r.url()).pathname)) scans.push(r.postDataJSON()); });
  const sheet = await share(page, [["till slip.png", "image/png"]]);
  await sheet.getByRole("button", { name: /Scan it/ }).click();
  await expect.poll(() => scans.length).toBe(1);
  expect(scans[0].image_base64.startsWith("data:image/png;base64,")).toBe(true);
});

test("nothing shared says so", async ({ page }) => {
  const sheet = await share(page, []);
  await expect(sheet.getByText("Nothing came through")).toBeVisible();
});
