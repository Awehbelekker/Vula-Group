// ai-usage.spec.js — the AI allowance (14 Nov 2026): Home shows "AI this month", Settings › AI usage
// shows the meter, what pauses, the packs, and a requested top-up waiting for payment. Phone,
// tablet and desktop: axe clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { VIEWPORTS, openScreen } from "./screens.js";

const SETTINGS = { name: "settings", role: "owner", nav: "Settings", ready: "Brand kit" };
const serious = async (page, sel) => (await new AxeBuilder({ page }).include(sel).analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`AI usage and a top-up request — ${vp}`, async ({ page }) => {
    await openScreen(page, SETTINGS, { viewport, theme: "light" });
    const asked = [];
    page.on("request", (r) => { if (r.method() === "POST" && /\/ai\/topup$/.test(new URL(r.url()).pathname)) asked.push(r.postDataJSON()); });
    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "AI usage" }).click();
    await expect(page.getByText("R85")).toBeVisible();
    await expect(page.getByText("of R100 used this month")).toBeVisible();
    const meter = page.getByRole("meter", { name: "AI allowance used" });
    await expect(meter).toHaveAttribute("aria-valuenow", "85");
    await expect(page.getByText(/Replies to your customers, reading your documents/)).toBeVisible();
    expect(await serious(page, ".vst-page")).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

    await page.getByRole("group", { name: "Top-up packs" }).getByRole("button", { name: "R250" }).click();
    await page.getByRole("button", { name: "Request top-up" }).click();
    await expect(page.getByText(/Top-up of R250 requested/)).toBeVisible();
    await expect(page.getByRole("list", { name: "Top-ups waiting for payment" }).getByText(/R250/)).toBeVisible();
    expect(asked).toEqual([{ amount_cents: 25000 }]);
  });
}

test("Home shows this month's AI next to the plan", async ({ page }) => {
  await openScreen(page, { name: "home", role: "owner", nav: null, ready: "Won in" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  const plan = page.getByRole("group", { name: "Your plan" });
  await expect(plan.getByText("AI this month: R85 of R100")).toBeVisible();
  await expect(plan.getByText(/Top up in Settings › AI usage/)).toBeVisible();
});
