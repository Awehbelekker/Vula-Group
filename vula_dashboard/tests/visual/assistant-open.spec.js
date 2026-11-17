// assistant-open.spec.js — the chat's "Ask anything" (13 Nov 2026): a mode toggle when the
// business has it switched on, the mode sent with each message, who answered, and Confirm /
// Cancel on an action Vula previewed. Phone, tablet and desktop: axe clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as F from "./fixtures/demo.js";
import { VIEWPORTS, openScreen } from "./screens.js";

F.assistantChat.openChat = true;          // this file only: each spec file runs in its own worker
const CHAT = { name: "chat", role: "owner", nav: "Assistant", tab: "Chat", ready: "What were today's sales?" };
const serious = async (page) => (await new AxeBuilder({ page }).include('section[aria-label="Chat to Vula"]').analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`ask anything, then a previewed action confirmed — ${vp}`, async ({ page }) => {
    await openScreen(page, CHAT, { viewport, theme: "light" });
    const sent = [];
    page.on("request", (r) => { if (r.method() === "POST" && /\/admin\/assistant$/.test(new URL(r.url()).pathname)) sent.push(r.postDataJSON()); });
    const chat = page.getByRole("region", { name: "Chat to Vula" });
    await chat.getByRole("tab", { name: "Ask anything" }).click();
    await expect(chat.getByText(/Ask anything: ideas, writing, planning/)).toBeVisible();
    await chat.getByLabel("Message Vula").fill("Give me ideas for a winter promotion");
    await chat.getByRole("button", { name: "Send" }).click();
    await expect(chat.getByText(/Five ideas for you/)).toBeVisible();
    await expect(chat.getByText("Vula's own AI", { exact: true })).toBeVisible();
    expect(sent[0]).toMatchObject({ message: "Give me ideas for a winter promotion", mode: "open" });

    await chat.getByLabel("Message Vula").fill("Change the stock for the 9ft bag to 4");
    await chat.getByRole("button", { name: "Send" }).click();
    const confirm = chat.getByRole("group", { name: "Confirm this action" });
    await expect(confirm).toBeVisible();
    await expect(chat.getByText("Answered with your business records", { exact: true })).toBeVisible();
    expect(await serious(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await confirm.getByRole("button", { name: "Confirm" }).click();
    await expect(chat.getByText("Stock for the 9ft bag is now 4.")).toBeVisible();
    await expect(confirm).toHaveCount(0);
  });
}

test("cancel leaves things as they were, and the mode is remembered", async ({ page }) => {
  await openScreen(page, CHAT, { viewport: VIEWPORTS.desktop, theme: "light" });
  const chat = page.getByRole("region", { name: "Chat to Vula" });
  await chat.getByRole("tab", { name: "Ask anything" }).click();
  await chat.getByLabel("Message Vula").fill("Update stock for the 9ft bag");
  await chat.getByRole("button", { name: "Send" }).click();
  await chat.getByRole("group", { name: "Confirm this action" }).getByRole("button", { name: "Cancel" }).click();
  await expect(chat.getByText("Cancelled — nothing was changed.")).toBeVisible();
  await expect(chat.getByRole("group", { name: "Confirm this action" })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("vula.assistant.mode"))).toBe("open");
});
