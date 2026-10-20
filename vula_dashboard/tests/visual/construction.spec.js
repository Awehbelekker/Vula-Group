// construction.spec.js — Operate › Workspace, Field ops, Labour and QS rates on the redesign's
// components (UX redesign Phase F, 2026-10-20). UI_V2=1: each screen GATES on axe and on no
// sideways scroll at 390px, and the requests match the current look's; every WhatsApp send is
// labelled, and the bulk briefing send asks first.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { VIEWPORTS, THEMES, openScreen } from "./screens.js";

const V2 = process.env.UI_V2 === "1";
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
  page.on("request", (r) => { const u = new URL(r.url()); if (r.method() === method && re.test(u.pathname)) seen.push({ path: u.pathname, body: r.postDataJSON?.() ?? null }); });
  return seen;
}

const MODULES = ["projects", "fieldops", "workspace", "estimating"];
const OP = (tab, ready) => ({ name: tab.toLowerCase(), role: "owner", nav: "Operate", tab, ready, modules: MODULES });
const SCREENS = [
  ["workspace", OP("Workspace", "R 18 400 of R 64 000 left")],
  ["fieldops", OP("Field Ops", "Pick a project")],
  ["labour", OP("Labour", "Lwazi")],
  ["qsrates", OP("QS Rates", "Drywall partition, 102mm")],
];

test.describe("redesign: construction (Operate)", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [name, screen] of SCREENS) {
    for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
      for (const theme of THEMES) {
        test(`${name} ${vp} ${theme}`, async ({ page }, ti) => {
          const errors = await openScreen(page, screen, { viewport, theme });
          await check(page, ti, `co-${name}`, `${vp}-${theme}`, viewport);
          expect(errors).toEqual([]);
        });
      }
    }
  }

  for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
    test(`field ops project with an open task ${vp}`, async ({ page }, ti) => {
      await openScreen(page, SCREENS[1][1], { viewport, theme: "dark" });
      await page.getByRole("combobox", { name: "Project", exact: true }).selectOption("pj-1");
      await page.getByRole("button", { name: /First-fix electrics/ }).click();
      await expect(page.getByRole("group", { name: "Sign off First-fix electrics" })).toBeVisible();
      await check(page, ti, "co-fieldops-task", `${vp}-dark`, viewport);
    });
  }

  test("workspace: chat, brief and to-dos send the same bodies", async ({ page }) => {
    await openScreen(page, SCREENS[0][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    const posts = capture(page, "POST", /\/v1\/projects\//);
    const puts = capture(page, "PUT", /\/brief$/);
    const patches = capture(page, "PATCH", /\/tasks\//);
    await page.getByLabel(/^Message about/).fill("Who still owes us?");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText("Two invoices are unpaid: INV-0041 and INV-0044.")).toBeVisible();
    expect(posts.find((p) => p.path.endsWith("/threads/th-1/messages")).body).toEqual({ project: "Bay_Events_catering", message: "Who still owes us?" });
    await page.getByLabel("Instructions for this project").fill("Gala for 240 now.");
    await page.getByRole("button", { name: "Save brief" }).click();
    await expect.poll(() => puts[0]?.body).toEqual({ brief: "Gala for 240 now." });
    await page.getByLabel("New to-do").fill("Book the generator");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/tasks"))?.body).toEqual({ title: "Book the generator" });
    await page.getByRole("checkbox", { name: "Confirm tent hire" }).click();
    await expect.poll(() => patches[0]?.body).toEqual({ status: "done" });
    await page.getByRole("tab", { name: "Board" }).click();
    await page.getByRole("tab", { name: "Chat" }).click();
    await expect(page.getByRole("button", { name: "Gala budget" })).toBeVisible();
  });

  test("field ops: chase, sign-off, assign, walkthrough and briefings; the bulk send asks first", async ({ page }) => {
    await openScreen(page, SCREENS[1][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    const posts = capture(page, "POST", /\/v1\/field\//);
    await page.getByRole("combobox", { name: "Project", exact: true }).selectOption("pj-1");
    await page.getByRole("button", { name: "Chase Install roof trusses on WhatsApp" }).click();
    await expect.poll(() => posts.map((p) => p.path)).toContain("/v1/field/task/ft-1/complete-request");
    await page.getByRole("button", { name: /First-fix electrics/ }).click();
    const sign = page.getByRole("group", { name: "Sign off First-fix electrics" });
    await sign.getByLabel("Review notes").fill("Neat work");
    await sign.getByRole("button", { name: "Approve" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/walkthrough/ft-2/approve"))?.body).toEqual({ approver_phone: "system-admin", decision: "approved", notes: "Neat work" });

    await page.getByRole("tab", { name: "Assign a task" }).click();
    const assign = page.getByRole("group", { name: "Assign a task" });
    await assign.getByRole("combobox", { name: "Project", exact: true }).selectOption("pj-1");
    await assign.getByLabel("Task", { exact: true }).fill("Hang the ceiling");
    await assign.getByLabel("Trade").fill("carpenter");
    await expect(assign.getByRole("button", { name: "Create task" })).toBeVisible();
    await assign.getByRole("combobox", { name: /^Contractor/ }).selectOption("fc-2");
    await assign.getByRole("button", { name: "Create & send on WhatsApp" }).click();
    await expect.poll(() => posts.find((p) => p.path === "/v1/field/task/assign")?.body).toEqual({ task_id: "ft-new", contractor_id: "fc-2", send_whatsapp: true });
    expect(posts.find((p) => p.path === "/v1/field/task").body).toMatchObject({ project_id: "pj-1", title: "Hang the ceiling", trade: "carpenter", contractor_id: "fc-2" });

    await page.getByRole("tab", { name: "Walkthrough" }).click();
    const walk = page.getByRole("group", { name: "Site walkthrough" });
    await walk.getByRole("combobox", { name: "Project", exact: true }).selectOption("pj-1");
    await walk.getByLabel("Walkthrough").fill("Roof inspection");
    await walk.getByRole("combobox", { name: /^Contractor/ }).selectOption("fc-2");
    await walk.getByLabel("Checklist").fill("Trusses\n\nWall plates");
    await walk.getByRole("button", { name: "Send walkthrough on WhatsApp" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/walkthrough/start"))?.body).toMatchObject({ project_id: "pj-1", title: "Roof inspection", contractor_id: "fc-2", items: ["Trusses", "Wall plates"] });
    await expect(walk.getByText("wt-77")).toBeVisible();

    await page.getByRole("tab", { name: "Briefings" }).click();
    await page.getByRole("button", { name: "Send briefings now" }).click();
    const dlg = page.getByRole("dialog").filter({ hasText: "Send today's WhatsApp briefing to 2 contractors now?" });
    await dlg.getByRole("button", { name: "Cancel" }).click();
    expect(posts.some((p) => p.path.endsWith("/dispatch"))).toBe(false);
    await page.getByRole("button", { name: "Send briefings now" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("2 briefings sent.")).toBeVisible();
  });

  test("labour and QS rates: same bodies; deletes ask first", async ({ page }) => {
    await openScreen(page, SCREENS[2][1], { viewport: VIEWPORTS.mobile, theme: "light" });
    const posts = capture(page, "POST", /\/(workers|qs\/rates\/)/);
    const deletes = capture(page, "DELETE", /./);
    await page.getByRole("button", { name: "Add a worker" }).click();
    const sheet = page.getByRole("dialog", { name: "Add a worker" });
    await sheet.getByRole("button", { name: "Add worker" }).click();
    await expect(sheet.getByRole("status")).toHaveText("Worker name is required.");
    await sheet.getByLabel("Full name").fill("Sizwe Mthembu");
    await sheet.getByLabel("Rate (R)").fill("480");
    await sheet.getByRole("button", { name: "Add worker" }).click();
    await expect(sheet).toBeHidden();
    expect(posts[0].body).toMatchObject({ name: "Sizwe Mthembu", rate_rands: 480, rate_period: "daily", type: "casual" });
    await page.getByRole("button", { name: "Remove Lwazi" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Yes" }).click();
    await expect.poll(() => deletes.map((d) => d.path.split("/").pop())).toEqual(["wk-1"]);

    await page.getByRole("tab", { name: /QS Rates$/ }).click();
    await page.getByRole("button", { name: "Update my rate" }).click();
    await expect.poll(() => posts.find((p) => p.path.includes("/qs/rates/"))?.body).toEqual({ id: "qr-1", description: "Drywall partition, 102mm", unit: "m2", rate: 540, source: "3 invoices, Sep 2026" });
    await page.getByRole("button", { name: "Delete Skim plaster" }).click();
    await page.getByRole("dialog").filter({ hasText: 'Delete your rate "Skim plaster"?' }).getByRole("button", { name: "Delete" }).click();
    await expect.poll(() => deletes.map((d) => d.path.split("/").pop())).toEqual(["wk-1", "qr-2"]);
  });
});

test("current look: Operate's construction tabs still load on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...SCREENS[3][1], ready: "QS Rate Library" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await expect(page.getByText("Drywall partition, 102mm")).toBeVisible();
  await page.getByRole("tab", { name: /Labour$/ }).first().click();
  await expect(page.getByText("acct 62001234567")).toBeVisible();
  await page.getByRole("tab", { name: /Field Ops$/ }).first().click();
  await expect(page.getByText("Field Operations")).toBeVisible();
});
