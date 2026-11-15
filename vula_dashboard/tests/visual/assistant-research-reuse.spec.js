// assistant-research-reuse.spec.js — research kept for the team (13 Nov 2026): a question
// researched before is answered "From research on <date>" with a Research again button (sent as
// fresh), the asker can keep a finding private or share it, and "Keep this research private" goes
// with the next research. Phone, tablet and desktop: axe clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as F from "./fixtures/demo.js";
import { VIEWPORTS, openScreen } from "./screens.js";

F.assistantChat.openChat = true;          // this file only: each spec file runs in its own worker
const CHAT = { name: "chat", role: "owner", nav: "Assistant", tab: "Chat", ready: "What were today's sales?" };
const serious = async (page) => (await new AxeBuilder({ page }).include('section[aria-label="Chat to Vula"]').analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`earlier research reused, then researched again — ${vp}`, async ({ page }) => {
    await openScreen(page, CHAT, { viewport, theme: "light" });
    const sent = [];
    const shares = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (r.method() !== "POST") return;
      if (/\/admin\/assistant$/.test(path)) sent.push(r.postDataJSON());
      if (/\/research\/[^/]+\/share$/.test(path)) shares.push({ path, body: r.postDataJSON() });
    });
    const chat = page.getByRole("region", { name: "Chat to Vula" });
    await chat.getByRole("tab", { name: "Ask anything" }).click();
    await chat.getByRole("button", { name: "Research the web" }).click();
    await chat.getByLabel("Message Vula").fill("How often should a timber deck be sealed?");
    await chat.getByRole("button", { name: "Send" }).click();
    await expect(chat.getByText("From research on 3 Nov 2026")).toBeVisible();
    const acts = chat.getByRole("group", { name: "This research" });
    await expect(acts.getByText("Shared with your team")).toBeVisible();
    expect(await serious(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

    await acts.getByRole("button", { name: "Keep private" }).click();
    await expect(acts.getByText("Only you")).toBeVisible();
    expect(shares[0]).toMatchObject({ body: { shared: false } });
    expect(shares[0].path).toMatch(/\/research\/rn-old\/share$/);

    await chat.getByLabel("Keep this research private").check();
    await acts.getByRole("button", { name: "Research again" }).click();
    await expect(chat.getByText(/Oil it every 9–12 months/)).toBeVisible();
    expect(sent[1]).toMatchObject({ message: "How often should a timber deck be sealed?", mode: "research", fresh: true, private: true });
    await expect(chat.getByText("Researched · 4 pages read")).toBeVisible();
    await expect(chat.getByRole("group", { name: "This research" }).last().getByText("Only you")).toBeVisible();
  });
}
