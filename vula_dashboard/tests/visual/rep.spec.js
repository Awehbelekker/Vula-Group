// rep.spec.js — a sales rep's own screens on the redesign's components (UX redesign Phase F,
// 2026-10-20): Home (with Today), Contacts, Call sheet, Reminders, Claim sheet, CRM sync. UI_V2=1:
// each screen GATES on axe and on no sideways scroll at 390px, and the requests match the
// current look's.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

const V2 = process.env.UI_V2 === "1";
const REP = "+27820000009";
const out = (ti, name) => path.join(ti.project.testDir, ".results", "migrate", name);
const bad = (r) => r.violations.filter((v) => ["serious", "critical"].includes(v.impact))
  .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ") + (process.env.AXE_DEBUG ? n.failureSummary : "")).join(" | ")}`);

async function check(page, ti, name, vp, viewport) {
  await page.screenshot({ path: out(ti, `${name}-${vp}.png`), fullPage: true, animations: "disabled" });
  expect(bad(await new AxeBuilder({ page }).include(".v2-content").analyze())).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}
function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (r.method() === method && re.test(u.pathname)) seen.push({ path: u.pathname, query: Object.fromEntries(u.searchParams), body: r.postDataJSON?.() ?? null });
  });
  return seen;
}

const MW = (tab, ready) => ({ name: tab.toLowerCase(), role: "rep", nav: "My Work", tab, ready });
const SCREENS = [
  ["home", { name: "rep-home", role: "rep", nav: null, ready: "Bay Hotel kitchens" }],
  ["contacts", MW("Contacts", "Lerato Khumalo")],
  ["callsheet", MW("Call Sheet", "Fairview: sample box Thursday")],
  ["reminders", MW("Reminders", "Send Bay Hotel the wholesale price list")],
  ["claimsheet", MW("Claim sheet", "This month so far")],
  ["crm", MW("CRM sync", "Search your real CRM data in Dynamics 365.")],
];

test.describe("redesign: sales rep", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [name, screen] of SCREENS) {
    for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
      for (const theme of THEMES) {
        test(`${name} ${vp} ${theme}`, async ({ page }, ti) => {
          const errors = await openScreen(page, screen, { viewport, theme });
          await check(page, ti, `rep-${name}`, `${vp}-${theme}`, viewport);
          expect(errors).toEqual([]);
        });
      }
    }
  }

  test("home: minutes and a new appointment send the same bodies", async ({ page }) => {
    await openScreen(page, SCREENS[0][1], { viewport: VIEWPORTS.mobile, theme: "light" });
    const posts = capture(page, "POST", /\/admin\//);
    await page.getByRole("button", { name: "Minutes for Bay Hotel kitchens" }).click();
    const sheet = page.getByRole("dialog", { name: "Meeting minutes" });
    await sheet.getByLabel("Minutes").fill("Agreed a tasting for 12 covers; send pricing by Friday.");
    await sheet.getByRole("button", { name: "Save minutes" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/meetings"))?.body).toEqual({
      notes: "Agreed a tasting for 12 covers; send pricing by Friday.", rep_phone: REP, booking_id: "bk-r2" });
    await expect(sheet).toBeHidden();

    await page.getByRole("button", { name: "Appointment", exact: true }).click();
    const add = page.getByRole("dialog", { name: "New appointment" });
    await add.getByLabel("Client or company").fill("Harbour Fish");
    await add.getByLabel("Where").fill("Kalk Bay");
    await add.getByRole("button", { name: "Add appointment" }).click();
    const body = await expect.poll(() => posts.find((p) => p.path.endsWith("/rep/appointments"))?.body).toBeTruthy().then(() => posts.find((p) => p.path.endsWith("/rep/appointments")).body);
    expect(body).toMatchObject({ customer_name: "Harbour Fish", location: "Kalk Bay", time: "10:00", duration_min: 60, rep_phone: REP });
    expect(body.start).toBe(`${body.date}T10:00`);

    await page.getByRole("button", { name: /^Open reminders/ }).click();
    await expect(page.getByText("Book the Fairview tasting")).toBeVisible();
  });

  test("my work: contacts, call sheet, reminders, claim sheet and CRM send the same requests", async ({ page }) => {
    await openScreen(page, SCREENS[1][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    const posts = capture(page, "POST", /\/admin\//);
    const gets = capture(page, "GET", /\/(dynamics365|admin)\//);
    const patches = capture(page, "PATCH", /\/admin\//);

    await page.getByRole("button", { name: "Add contact" }).click();
    const sheet = page.getByRole("dialog", { name: "Add a contact" });
    await sheet.getByRole("button", { name: "Save contact" }).click();
    await expect(sheet.getByRole("alert")).toHaveText("Name is required.");
    await sheet.getByLabel("Name").fill("Nomsa Dlamini");
    await sheet.getByLabel("Company").fill("Harbour Fish");
    await sheet.getByRole("button", { name: "Save contact" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/admin/contacts"))?.body).toEqual({
      name: "Nomsa Dlamini", phone: "", email: "", company: "Harbour Fish", title: "", notes: "", created_by: REP });
    await expect(sheet).toBeHidden();

    await page.getByRole("tab", { name: /Call Sheet$/ }).click();
    const email = page.getByLabel("Email it to");
    await email.focus(); await email.blur();                         // untouched: nothing saved
    await email.fill("rep-sheets@harbourvine.example.test"); await email.blur();
    await page.getByLabel("Every").selectOption("0");
    await expect.poll(() => posts.filter((p) => p.path.endsWith("/call-sheet/configure")).map((p) => p.body)).toEqual([
      { rep_phone: REP, recipient_email: "rep-sheets@harbourvine.example.test" }, { rep_phone: REP, day_of_week: 0 }]);
    await page.getByLabel("What should change?").fill("the Atlantic meeting was about self-levelling");
    await page.getByRole("button", { name: "Preview change" }).click();
    await expect(page.getByText("Atlantic Dairy entry: topic changed to self-levelling")).toBeVisible();
    await page.getByRole("button", { name: "Confirm" }).click();
    await expect.poll(() => posts.filter((p) => p.path.endsWith("/call-sheet/update")).map((p) => p.body.confirm)).toEqual([false, true]);

    await page.getByRole("tab", { name: /Reminders$/ }).click();
    await expect(page.getByText("Overdue", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Done: Send Bay Hotel the wholesale price list" }).click();
    await expect.poll(() => patches.map((p) => p.path.split("/").pop())).toEqual(["rm-1"]);

    await page.getByRole("tab", { name: /Claim sheet$/ }).click();
    await expect(page.getByText("Getting close to your monthly budget.")).toBeVisible();
    const budget = page.getByLabel("Monthly budget (R)");
    await budget.fill("4000"); await budget.blur();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/expense-sheet/configure"))?.body).toEqual({ rep_phone: REP, budget_rands: 4000 });

    await page.getByRole("tab", { name: /CRM sync$/ }).click();
    await page.getByRole("tab", { name: "Accounts" }).click();
    await page.getByLabel("Search Dynamics 365").fill("atlantic");
    await page.getByLabel("Search Dynamics 365").press("Enter");
    await expect.poll(() => gets.find((g) => g.path.endsWith("/search"))?.query).toEqual({ query: "atlantic", kind: "account" });
    await expect(page.getByText("Lerato Khumalo")).toBeVisible();
  });
});

test("current look: a rep's Home and My Work still load on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { name: "rep-home", role: "rep", nav: null, ready: "Bay Hotel kitchens" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText("⏰ Reminders (3 open)")).toBeVisible();
  await expect(page.getByText("Goes to sales@harbourvine.example.test")).toBeVisible();
});
