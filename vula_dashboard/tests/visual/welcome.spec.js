// welcome.spec.js — the first-run welcome on Home (Welcome.jsx, 12 Nov 2026): optional ("Just my
// website" by default, "Website and documents", or "Skip for now"), then what Vula found, the
// answers to keep, and a customer's question answered with its source. Every width: no sideways
// scroll, axe clean, nothing under the phone's bottom bar.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as F from "./fixtures/demo.js";
import { VIEWPORTS, openScreen } from "./screens.js";

F.firstRun.show = true;            // this file only: each spec file runs in its own worker
const HOME = { name: "home", role: "owner", nav: null, ready: "Won in" };
const serious = async (page) => (await new AxeBuilder({ page }).include(".vwl").analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);
const fits = async (page, viewport) => expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`just my website, through to an answer with its source — ${vp}`, async ({ page }) => {
    const errors = await openScreen(page, HOME, { viewport, theme: "light" });
    const w = page.getByRole("region", { name: "Let Vula set you up" });
    await expect(w.getByRole("radio", { name: "Just my website" })).toHaveAttribute("aria-checked", "true");
    await expect(w.getByRole("button", { name: "Add documents" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Make it yours" })).toHaveCount(0);       // stands aside
    expect(await serious(page)).toEqual([]);
    await w.getByLabel("Your website").fill("harbourvine.example");
    await w.getByRole("button", { name: "Set me up" }).click();
    await expect(w.getByText("Terms", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(w.getByText("12 products, 11 with photos, 12 with descriptions")).toBeVisible();
    await expect(w.getByText("Returns policy: not found on the website")).toBeVisible();
    await expect(w.getByText("Not found: social pages")).toBeVisible();
    const found = w.getByRole("region", { name: "Answers Vula found for you to check" });
    await found.getByRole("button", { name: "Keep all" }).click();
    await expect(found).toHaveCount(0);
    await w.getByRole("button", { name: "What are your opening hours?" }).click();
    await expect(w.getByText("From: Business profile — for customers")).toBeVisible();
    expect(await serious(page)).toEqual([]);
    await fits(page, viewport);
    // scrolled to the end, the last button clears the phone's bottom bar
    const clear = await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
      const bar = document.querySelector(".vui-toolbar")?.getBoundingClientRect();
      const done = [...document.querySelectorAll(".vwl button")].pop().getBoundingClientRect();
      return !bar || done.bottom <= bar.top;
    });
    expect(clear).toBe(true);
    await w.getByRole("button", { name: "Done — take me to my dashboard" }).click();
    await expect(w).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Make it yours" }).or(page.getByRole("region", { name: "Finish setting up" })).first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test(`skip for now stays skipped and set-up is still offered — ${vp}`, async ({ page }) => {
    await openScreen(page, HOME, { viewport, theme: "light" });
    await page.getByRole("region", { name: "Let Vula set you up" }).getByRole("button", { name: "Skip for now" }).click();
    await expect(page.getByRole("region", { name: "Let Vula set you up" })).toHaveCount(0);
    const card = page.getByRole("region", { name: "Finish setting up" });
    await expect(card.getByRole("button", { name: "Upload documents" })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Won in").first()).toBeVisible();
    await expect(page.getByRole("region", { name: "Let Vula set you up" })).toHaveCount(0);
  });
}

test("website and documents: both are read and their answers listed", async ({ page }) => {
  await openScreen(page, HOME, { viewport: VIEWPORTS.desktop, theme: "light" });
  const w = page.getByRole("region", { name: "Let Vula set you up" });
  await w.getByRole("radio", { name: "Website and documents" }).click();
  await w.getByLabel("Choose company documents").setInputFiles({ name: "Terms of trade.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
  await expect(w.getByText("Terms of trade.pdf", { exact: true })).toBeVisible();
  await w.getByLabel("Your website").fill("harbourvine.example");
  await w.getByRole("button", { name: "Set me up" }).click();
  await expect(w.getByText("Terms of trade.pdf: 2 answers found")).toBeVisible({ timeout: 15000 });
  await expect(w.getByRole("region", { name: "Answers Vula found for you to check" })).toBeVisible();
});

test("nothing given: it asks for the website instead of starting", async ({ page }) => {
  await openScreen(page, HOME, { viewport: VIEWPORTS.desktop, theme: "light" });
  const w = page.getByRole("region", { name: "Let Vula set you up" });
  await w.getByRole("button", { name: "Set me up" }).click();
  await expect(w.getByRole("alert")).toHaveText("Add your website address.");
});
