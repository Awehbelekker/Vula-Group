// assistant.spec.js — the Assistant hub on the redesign's components (UX redesign Phase F,
// 2026-10-20): Chat, Activity, Automations, Flows. UI_V2=1: each screen GATES on axe and on no
// sideways scroll at 390px, and the request bodies match the current look's.
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
function capture(page, method, re) {
  const seen = [];
  page.on("request", (r) => { if (r.method() === method && re.test(new URL(r.url()).pathname)) seen.push({ path: new URL(r.url()).pathname, body: r.postDataJSON?.() ?? null }); });
  return seen;
}

const AS = (tab, ready) => ({ name: tab.toLowerCase(), role: "owner", nav: "Assistant", tab, ready, modules: ["flows"] });
const SCREENS = [
  ["chat", AS("Chat", "What were today's sales?")],
  ["activity", AS("Activity", "Marked order HV-1052 as dispatched")],
  ["automations", AS("Automations", "Waiting for your approval")],
  ["flows", AS("Flows", "Book a tasting")],
];

test.describe("redesign: assistant hub", () => {
  test.skip(!V2, "the redesign runs with UI_V2=1");

  for (const [name, screen] of SCREENS) {
    for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
      for (const theme of THEMES) {
        test(`${name} ${vp} ${theme}`, async ({ page }, ti) => {
          const errors = await openScreen(page, screen, { viewport, theme });
          await check(page, ti, `as-${name}`, `${vp}-${theme}`, viewport);
          expect(errors).toEqual([]);
        });
      }
    }
  }

  test("chat: a suggestion and a typed message go to the same session", async ({ page }) => {
    await openScreen(page, SCREENS[0][1], { viewport: VIEWPORTS.mobile, theme: "light" });
    const sent = capture(page, "POST", /\/admin\/assistant$/);
    await page.getByRole("button", { name: "What were today's sales?" }).click();
    await expect(page.getByText("Today: 4 orders, R 1 842.50 paid. (What were today's sales?)")).toBeVisible();
    await page.getByLabel("Message Vula").fill("Mark HV-1052 as dispatched");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => sent.length).toBe(2);
    expect(sent[1].body.message).toBe("Mark HV-1052 as dispatched");
    expect(sent[0].body.session_id).toBe(sent[1].body.session_id);
  });

  test("activity, automations and flows send the same bodies; deletes ask first", async ({ page }) => {
    await openScreen(page, SCREENS[1][1], { viewport: VIEWPORTS.desktop, theme: "light" });
    const posts = capture(page, "POST", /\/admin\//);
    const patches = capture(page, "PATCH", /\/admin\//);
    await page.getByRole("button", { name: "Accept" }).click();
    await expect.poll(() => patches[0]?.body).toEqual({ persona_prompt: "Friendly and brief, says 'shot!' for thanks, signs off with the shop name." });
    await page.getByLabel("Question or topic").fill("Do you deliver to Stellenbosch?");
    await page.getByLabel("The correct answer").fill("Yes, on Fridays, R80.");
    await page.getByRole("button", { name: "Teach it" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/agent-teach"))?.body).toEqual({ question: "Do you deliver to Stellenbosch?", answer: "Yes, on Fridays, R80." });

    await page.getByRole("tab", { name: "Automations" }).click();
    await page.getByRole("button", { name: "Approve & send" }).click();
    await expect.poll(() => posts.some((p) => /firings\/fi-1\/approve$/.test(p.path))).toBe(true);
    await page.getByRole("button", { name: "New automation" }).click();
    const form = page.getByRole("group", { name: "New automation" });
    await form.getByLabel("When").selectOption("low_stock");
    await form.getByLabel("Message to send").fill("{{product_name}} is down to {{stock}}");
    await form.getByRole("button", { name: "Create automation" }).click();
    await expect(form.getByRole("alert")).toContainText("low-stock trigger");
    await form.getByLabel("Then").selectOption("whatsapp_team");
    await form.getByRole("button", { name: "Create automation" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/admin/automations"))?.body).toEqual({
      trigger_type: "low_stock", trigger_config: {}, action_type: "whatsapp_team", action_config: { message: "{{product_name}} is down to {{stock}}" } });
    await page.getByRole("switch", { name: "Cheddar running low on" }).click();
    await expect.poll(() => patches.find((p) => p.path.endsWith("/automations/au-2"))?.body).toEqual({ enabled: true });
    const deleted = [];
    page.on("request", (r) => { if (r.method() === "DELETE") deleted.push(new URL(r.url()).pathname.split("/").slice(-2).join("/")); });
    await page.getByRole("button", { name: "Delete On its way" }).click();
    await page.getByRole("dialog").filter({ hasText: 'Delete automation "On its way"?' }).getByRole("button", { name: "Yes" }).click();

    await page.getByRole("tab", { name: "Flows" }).click();
    await page.getByRole("button", { name: "New flow" }).click();
    await page.getByLabel("Flow name").fill("Book a demo");
    await page.getByLabel("Trigger phrases").fill("book a demo, demo");
    await page.getByRole("group", { name: "Step 1" }).getByLabel("What should Vula ask?").fill("Which day suits you?");
    await page.getByRole("button", { name: "Add step" }).click();
    const s2 = page.getByRole("group", { name: "Step 2" });
    await s2.getByLabel("What should Vula ask?").fill("Morning or afternoon?");
    await s2.getByLabel("Reply type").selectOption("multiple_choice");
    await s2.getByLabel("Options").fill("Morning, Afternoon");
    await page.getByRole("button", { name: "Create flow" }).click();
    await expect.poll(() => posts.find((p) => p.path.endsWith("/admin/flows"))?.body).toEqual({
      name: "Book a demo", trigger_phrases: ["book a demo", "demo"],
      steps: [
        { prompt: "Which day suits you?", reply_type: "free_text", branches: {}, default_next: 1 },
        { prompt: "Morning or afternoon?", reply_type: "multiple_choice", options: ["Morning", "Afternoon"], branches: {}, default_next: null },
      ],
      end_action: { tool: "notify_team", arg_map: {}, closing_message: "Thanks — we’ve got your details!" },
    });
    await page.getByRole("button", { name: "Delete Book a tasting" }).click();
    await page.getByRole("dialog").filter({ hasText: 'Delete flow "Book a tasting"?' }).getByRole("button", { name: "Yes" }).click();
    await expect.poll(() => deleted.sort()).toEqual(["automations/au-1", "flows/fl-1"]);
  });
});

test("current look: the Assistant hub still loads on the shared hooks", async ({ page }) => {
  test.skip(V2, "the current look runs without UI_V2");
  await openScreen(page, { ...SCREENS[3][1], nav: "Assistant" }, { viewport: VIEWPORTS.desktop, theme: "light" });
  await page.getByRole("tab", { name: /Automations$/ }).first().click();
  await expect(page.getByText("On its way", { exact: true })).toBeVisible();
});
