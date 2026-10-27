// app-errors.spec.js — a crash on someone's device reaches Master › Health (2026-10-27,
// src/lib/appErrors.js → POST /v1/app-errors): with the screen and the business, without the
// personal details in the message, once, and never the noise that isn't Vula's to fix. Either
// design (UI_V2=1 for the redesign).
import { test, expect } from "@playwright/test";
import { installMockApi, seedSession } from "./mockApi.js";
import { NOW_ISO } from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";

async function open(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.clock.setFixedTime(new Date(NOW_ISO));
  await installMockApi(page, { role: "owner" });
  await seedSession(page, { role: "owner", theme: "light" });
  const reports = [];
  page.on("request", (r) => { if (r.method() === "POST" && /\/v1\/app-errors$/.test(new URL(r.url()).pathname)) reports.push(r.postDataJSON()); });
  await page.goto("/");
  await page.locator(V2 ? ".v2-shell" : ".vshell, .v2-shell").first().waitFor();
  return reports;
}

// An error thrown from one of Vula's own files, as the browser reports it.
const crash = (page, message, frame = "at Orders (http://localhost/src/components/VulaInvoices.jsx:12:5)") =>
  page.evaluate(([m, f]) => {
    const err = new Error(m);
    err.stack = `Error: ${m}\n    ${f}`;
    window.dispatchEvent(new ErrorEvent("error", { error: err, message: m }));
  }, [message, frame]);

test("a crash is reported once, with the screen and the business, without the customer's details", async ({ page }) => {
  const reports = await open(page);
  await crash(page, "Couldn't price order for thandi@bayevents.co.za on 082 123 4567");
  await crash(page, "Couldn't price order for thandi@bayevents.co.za on 082 123 4567");   // the same again
  await expect.poll(() => reports.length).toBe(1);
  const r = reports[0];
  expect(r.message).toBe("uncaught: Couldn't price order for [email] on [n]");
  expect(r.page).toBe("overview");
  expect(r.tenant_id).toBe("demo-shop");
  expect(r.stack).toContain("/src/components/VulaInvoices.jsx:12:5");
  expect(r.build).toBeTruthy();
});

test("noise isn't reported: other sites, extensions, the layout warning, a dropped connection", async ({ page, context }) => {
  const reports = await open(page);
  await crash(page, "ResizeObserver loop completed with undelivered notifications.");
  await crash(page, "boom", "at inject (chrome-extension://abcdef/inject.js:1:1)");
  await crash(page, "TypeError: Failed to fetch");
  await context.setOffline(true);
  await crash(page, "x is undefined");
  await context.setOffline(false);
  await page.waitForTimeout(300);
  expect(reports).toEqual([]);
});
