// money.spec.js — Money › Tap to Pay and Budget on the redesign's components (UX redesign Phase F,
// 2026-10-20). UI_V2=1: each screen GATES on axe (serious/critical = fail) and on no sideways scroll
// at 390px, and the request bodies match the current look's. Typed rands become integer cents.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";
import * as F from "./fixtures/demo.js";

const V2 = process.env.UI_V2 === "1";
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => { if (r.method() === method && re.test(new URL(r.url()).pathname)) seen.push({ path: new URL(r.url()).pathname, body: r.postDataJSON?.() ?? null }); });
  return seen;
}

const TAP = { name: "taptopay", role: "owner", nav: "Money", tab: "Tap to Pay", ready: "Who gets paid, and their tag", modules: ["taptopay"] };
const BUDGET = { name: "budget", role: "owner", nav: "Money", tab: "Budget", ready: "Recurring bills", modules: ["budget"] };

test.describe("redesign: money", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [name, screen] of [["taptopay", TAP], ["budget", BUDGET]]) {
    for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
      for (const theme of THEMES) {
        test(`${name} ${vp} ${theme}`, async ({ page }, ti) => {
          const errors = await openScreen(page, screen, { viewport, theme });
          await check(page, ti, name, `${vp}-${theme}`, viewport);
          expect(errors).toEqual([]);
        });
      }
    }
  }

  test("tap to pay: shares, tags, codes, bills and the asks before a change that can't be undone", async ({ page }) => {
    await openScreen(page, TAP, { viewport: VIEWPORTS.mobile, theme: "light" });
    const posts = capture(page, "POST", /^\/v1\/(tap|payments)\//);
    const puts = capture(page, "PUT", /^\/v1\/tap\//);
    const T = F.TENANT_ID;

    await page.getByLabel("Share for Lerato (coach) (%)").fill("62.5");
    await page.getByRole("button", { name: "Save shares" }).click();
    await expect.poll(() => puts.length).toBe(1);
    expect(puts[0].body).toEqual({ default_share_bp: 0, staff_shares: { "tm-1": 6250, "tm-2": 5000 } });

    await page.getByRole("button", { name: "Create a tag for Sipho (coach)" }).click();
    await expect.poll(() => posts.map((p) => p.path)).toContain(`/v1/tap/${T}/staff/tm-2/tag`);

    // replacing a tag asks first; Cancel sends nothing
    await page.getByRole("button", { name: "Replace Lerato (coach)'s tag" }).click();
    await page.getByRole("dialog").filter({ hasText: "Make a new tag?" }).getByRole("button", { name: "Cancel" }).click();
    expect(posts.some((p) => p.path.endsWith("/tm-1/tag"))).toBe(false);
    await page.getByRole("button", { name: "Replace Lerato (coach)'s tag" }).click();
    await page.getByRole("dialog").filter({ hasText: "Make a new tag?" }).getByRole("button", { name: "Make a new tag" }).click();
    await expect.poll(() => posts.map((p) => p.path)).toContain(`/v1/tap/${T}/staff/tm-1/tag`);

    await page.getByRole("button", { name: "Get a sign-in code for Lerato (coach)" }).click();
    await expect(page.getByText("482915")).toBeVisible();
    await expect(page.getByRole("button", { name: "Get a sign-in code for Sipho (coach)" })).toBeDisabled();

    await page.getByRole("button", { name: "Sign out Lerato (coach)'s phone" }).click();
    await page.getByRole("dialog").filter({ hasText: "Sign this phone out?" }).getByRole("button", { name: "Sign out" }).click();
    await expect.poll(() => posts.map((p) => p.path)).toContain(`/v1/tap/${T}/devices/dv-1/revoke`);

    const bill = page.getByRole("group", { name: "Unpaid: Board hire" });
    await bill.getByRole("button", { name: "Write off" }).click();
    await page.getByRole("dialog").filter({ hasText: "Write this bill off?" }).getByRole("button", { name: "Write off" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/ub-2/close"))?.body).toEqual({ action: "write_off" });
    await page.getByRole("group", { name: "Unpaid: Beginner lesson" }).getByRole("button", { name: "Paid another way" }).click();
    await page.getByRole("group", { name: "Unpaid: Beginner lesson" }).getByRole("button", { name: "EFT" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/ub-1/close"))?.body).toEqual({ action: "paid_other", reason: "eft" });

    await page.getByLabel("Who served?").selectOption("tm-1");
    await page.getByLabel("What for?").fill("Private lesson");
    await page.getByLabel("Amount (R)").fill("750.50");
    await page.getByRole("button", { name: "Create bill" }).click();
    await expect.poll(() => posts.find((p) => p.path === `/v1/tap/${T}/bills`)?.body)
      .toEqual({ amount_cents: 75_050, description: "Private lesson", staff_id: "tm-1", customer_phone: null });
    await expect(page.getByText("give them code K7Q")).toBeVisible();
    await page.getByRole("button", { name: "Go live" }).click();
    await expect(page.getByText("Customers can tap and pay now.")).toBeVisible();
  });

  test("budget: add, pay, delete (asks) and recurring bills send the same bodies", async ({ page }) => {
    await openScreen(page, BUDGET, { viewport: VIEWPORTS.desktop, theme: "light" });
    const posts = capture(page, "POST", /\/(admin\/expenses|recurring-bills)/);
    const others = [];
    page.on("request", (r) => { if (["PATCH", "DELETE"].includes(r.method())) others.push(`${r.method()} ${new URL(r.url()).pathname.split("/").slice(-2).join("/")}`); });

    await page.getByRole("button", { name: "Mark Fairview cheese account paid" }).click();
    await page.getByRole("button", { name: "Add expense" }).click();
    const sheet = page.getByRole("dialog", { name: "Add an expense" });
    await sheet.getByLabel("Description").fill("Ice for the display");
    await sheet.getByLabel("Category").selectOption("stock");
    await sheet.getByLabel("Amount (R)").fill("185.40");
    await sheet.getByLabel("Due date").fill("2026-10-30");
    await page.getByRole("button", { name: "Save expense" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/admin/expenses"))?.body).toMatchObject({
      category: "stock", description: "Ice for the display", amount_cents: 18_540, supplier: "", due_date: "2026-10-30" });

    await page.getByRole("button", { name: "Delete Sourdough flour, 25kg" }).click();
    await page.getByRole("dialog").filter({ hasText: "Delete this expense?" }).getByRole("button", { name: "Cancel" }).click();
    expect(others.filter((o) => o.startsWith("DELETE"))).toHaveLength(0);
    await page.getByRole("button", { name: "Delete Sourdough flour, 25kg" }).click();
    await page.getByRole("dialog").filter({ hasText: "Delete this expense?" }).getByRole("button", { name: "Delete" }).click();

    await page.getByRole("button", { name: "Add", exact: true }).click();
    const rec = page.getByRole("group", { name: "Recurring bills" });
    await rec.getByLabel("Description").fill("Fibre line");
    await rec.getByLabel("Category").selectOption("utilities");
    await rec.getByLabel("Amount (R)").fill("899");
    await page.getByRole("button", { name: "Save recurring bill" }).click();
    await expect.poll(() => posts.find((p) => /recurring-bills\/[^/]+$/.test(p.path))?.body).toMatchObject({
      description: "Fibre line", supplier: null, category: "utilities", amount_cents: 89_900, cadence: "monthly" });
    await page.getByRole("button", { name: "Stop the recurring bill POS software" }).click();
    await page.getByRole("dialog").filter({ hasText: "Stop this recurring bill?" }).getByRole("button", { name: "Yes" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/rb-2/status"))?.body).toEqual({ status: "cancelled" });
    await expect.poll(() => others.sort()).toEqual(["DELETE expenses/exp-009", "PATCH exp-due-1/pay"]);
  });
});

test("current look: Tap to Pay and Budget still load on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...TAP, nav: "Money", modules: ["taptopay", "budget"] }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText("Lerato (coach)").first()).toBeVisible();
  await page.getByRole("tab", { name: /Budget$/ }).first().click();
  await expect(page.getByText("POS software")).toBeVisible();
  await expect(page.getByText("Sourdough flour, 25kg")).toBeVisible();
});
