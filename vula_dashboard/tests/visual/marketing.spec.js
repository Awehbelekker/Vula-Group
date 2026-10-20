// marketing.spec.js — Marketing on the redesign's components (UX redesign Phase F, 2026-10-20).
// UI_V2=1 runs the redesign: each screen GATES on axe (serious/critical = fail) and on no sideways
// scroll at 390px. Broadcast messages customers, so its tests also pin the guard rails: nothing is
// sent without the audience preview first, and the request bodies match the current look's.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

const V2 = process.env.UI_V2 === "1";
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}

/** Requests to a path, as parsed JSON bodies. */
function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => { if (r.method() === method && re.test(new URL(r.url()).pathname)) seen.push(r.postDataJSON()); });
  return seen;
}

const BROADCAST = { name: "broadcast", role: "owner", nav: "Marketing", tab: "Broadcast", ready: "New broadcast" };

test.describe("redesign: broadcast", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    for (const theme of THEMES) {
      test(`broadcast ${vp} ${theme}`, async ({ page }, ti) => {
        const errors = await openScreen(page, BROADCAST, { viewport, theme });
        await expect(page.getByText("Lapsed regulars")).toBeVisible();
        await check(page, ti, "broadcast", `${vp}-${theme}`, viewport);
        await page.getByRole("tab", { name: "Approved template" }).click();
        await page.getByRole("combobox", { name: "Template", exact: true }).selectOption("weekly_specials");
        await page.getByRole("button", { name: "Preview audience" }).click();
        await expect(page.getByRole("button", { name: "Send live to 184" })).toBeVisible();
        await check(page, ti, "broadcast-confirm", `${vp}-${theme}`, viewport);
        await page.getByRole("button", { name: /Weekly fish specials/ }).click();
        await expect(page.getByText("from 9 orders")).toBeVisible();
        await check(page, ti, "broadcast-history", `${vp}-${theme}`, viewport);
        expect(errors).toEqual([]);
      });
    }
  }

  test("broadcast: free text is never sent; an approved template goes only after the preview", async ({ page }) => {
    await openScreen(page, BROADCAST, { viewport: VIEWPORTS.mobile, theme: "light" });
    const sends = capture(page, "POST", /\/broadcasts\/send$/);

    // free text: write with AI, then a test send is refused before any request
    await page.getByLabel("Your details (Vula writes it for you)").fill("yellowtail R180, snoek R95");
    await page.getByRole("button", { name: "Write with AI" }).click();
    await expect(page.getByLabel("Message to send")).toHaveValue(/Fresh this week/);
    await page.getByLabel("Send a test to").fill("27820000001");
    await page.getByRole("button", { name: "Send test" }).click();
    await expect(page.getByRole("alert")).toContainText("WhatsApp only allows broadcasts from an approved template");
    // free text can still be previewed (a dry run), never sent live
    await page.getByRole("button", { name: "Preview audience" }).click();
    await expect.poll(() => sends.length).toBe(1);
    expect(sends[0]).toMatchObject({ dry_run: true, audience_filter: "all" });
    expect(sends[0].body).toMatch(/Fresh this week/);
    await page.getByRole("button", { name: "Send live to 184" }).click();
    await expect(page.getByRole("alert")).toContainText("approved template");
    expect(sends).toHaveLength(1);
    await page.getByRole("button", { name: "Cancel" }).first().click();

    // approved template: test, preview, then the confirmed live send
    await page.getByRole("tab", { name: "Approved template" }).click();
    await page.getByRole("combobox", { name: "Template", exact: true }).selectOption("weekly_specials");
    await page.getByRole("checkbox", { name: /Active \(last 30 days\)/ }).check();
    await page.getByRole("checkbox", { name: /Lapsed regulars/ }).check();
    await page.getByLabel("Link to track clicks on").fill("https://harbourvine.example.test/menu");
    await page.getByRole("button", { name: "Send test" }).click();
    await expect.poll(() => sends.length).toBe(2);
    expect(sends[1]).toEqual({ template_name: "weekly_specials", target_url: "https://harbourvine.example.test/menu", test_phone: "27820000001", dry_run: false });
    await expect(page.getByText("Test sent to 27820000001")).toBeVisible();

    await page.getByRole("button", { name: "Preview audience" }).click();
    await expect.poll(() => sends.length).toBe(3);
    expect(sends[2]).toEqual({ template_name: "weekly_specials", audience_filter: "all,active_30d,seg:sg-1", dry_run: true });
    await expect(page.getByText("3 opted-out contacts left out (POPIA).")).toBeVisible();
    await page.getByRole("button", { name: "Send live to 184" }).click();
    await expect.poll(() => sends.length).toBe(4);
    expect(sends[3]).toEqual({ template_name: "weekly_specials", audience_filter: "all,active_30d,seg:sg-1", name: "weekly_specials",
      target_url: "https://harbourvine.example.test/menu", dry_run: false });
    await expect(page.getByText("Sent to 184 recipients.")).toBeVisible();
  });

  test("broadcast: an image template needs its image; segments and schedules send the same bodies", async ({ page }) => {
    await openScreen(page, BROADCAST, { viewport: VIEWPORTS.desktop, theme: "light" });
    const sends = capture(page, "POST", /\/broadcasts\/send$/);
    const segs = capture(page, "POST", /\/admin\/segments$/);
    const sched = capture(page, "POST", /\/admin\/campaigns$/);
    const deleted = [];
    page.on("request", (r) => { if (r.method() === "DELETE") deleted.push(new URL(r.url()).pathname.split("/").slice(-2).join("/")); });

    await page.getByRole("tab", { name: "Approved template" }).click();
    await page.getByRole("combobox", { name: "Template", exact: true }).selectOption("promo_image");
    await page.getByRole("button", { name: "Preview audience" }).click();
    await page.getByRole("button", { name: "Send live to 184" }).click();
    await expect(page.getByRole("alert")).toContainText("image header");
    expect(sends.filter((b) => !b.dry_run)).toHaveLength(0);

    await page.getByRole("button", { name: "New segment" }).click();
    await page.getByLabel("Segment name").fill("Atlantic Seaboard");
    await page.getByLabel("Area").fill("Sea Point");
    await page.getByLabel("Language").selectOption("af");
    await page.getByRole("button", { name: "Save segment" }).click();
    await expect.poll(() => segs.length).toBe(1);
    expect(segs[0]).toEqual({ name: "Atlantic Seaboard", criteria: { area: "Sea Point", language: "af" } });
    await page.getByRole("button", { name: "Delete segment Lapsed regulars" }).click();

    await page.getByRole("tab", { name: "Free text" }).click();
    await page.getByLabel("Kind of message").selectOption("reorder");
    await page.getByLabel("Message to send").fill("Time to stock up? Reply to order.");
    await page.getByLabel("When").fill("2026-10-23T16:30");
    await page.getByLabel("Repeats").selectOption("weekly");
    await page.getByRole("button", { name: "Schedule", exact: true }).click();
    await expect.poll(() => sched.length).toBe(1);
    expect(sched[0]).toMatchObject({ name: "🔔 Reorder reminder", body: "Time to stock up? Reply to order.", audience_filter: "all", recurrence: "weekly" });
    expect(new Date(sched[0].run_at).toISOString()).toBe(sched[0].run_at);
    await page.getByRole("button", { name: "Cancel Reorder reminder" }).click();
    await expect.poll(() => deleted.sort()).toEqual(["campaigns/cp-1", "segments/sg-1"]);
  });

  test("picture promotion: preview, test and a confirmed send", async ({ page }) => {
    await openScreen(page, BROADCAST, { viewport: VIEWPORTS.mobile, theme: "light" });
    const promo = capture(page, "POST", /\/broadcasts\/promo$/);
    const card = page.getByRole("group", { name: "Picture promotion" });
    await card.getByLabel("Your offer").fill("15% off platters this weekend!");
    await card.getByLabel("Who gets it").selectOption("high_value");
    await card.getByRole("button", { name: "Preview" }).click();
    await expect(card.getByText(/Reaches 97 customers \(2 opted out, left out\)/)).toBeVisible();
    await card.getByLabel("Test to your number").fill("27820000001");
    await card.getByRole("button", { name: "Test to me" }).click();
    await expect(card.getByText("Test sent to 27820000001")).toBeVisible();
    await card.getByRole("button", { name: "Send promotion to 97" }).click();
    const dlg = page.getByRole("dialog").filter({ hasText: "Send this promotion to 97 customers?" });
    await dlg.getByRole("button", { name: "Cancel" }).click();
    expect(promo.map((b) => b.dry_run)).toEqual([true, false]);
    await card.getByRole("button", { name: "Send promotion to 97" }).click();
    await dlg.getByRole("button", { name: "Send promotion" }).click();
    await expect.poll(() => promo.length).toBe(3);
    expect(promo[2]).toEqual({ message: "15% off platters this weekend!", audience_filter: "high_value", dry_run: false });
    await expect(card.getByText("Sending to 97 customers")).toBeVisible();
  });
});
