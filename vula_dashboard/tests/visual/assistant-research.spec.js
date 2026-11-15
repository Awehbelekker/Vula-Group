// assistant-research.spec.js — research mode in Ask anything (13 Nov 2026): a "Research the web"
// toggle, the message sent as mode "research", and the answer's sources listed as links under it
// (with the business's own documents marked). Phone, tablet and desktop: axe clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as F from "./fixtures/demo.js";
import { VIEWPORTS, openScreen } from "./screens.js";

F.assistantChat.openChat = true;          // this file only: each spec file runs in its own worker
const CHAT = { name: "chat", role: "owner", nav: "Assistant", tab: "Chat", ready: "What were today's sales?" };
const serious = async (page) => (await new AxeBuilder({ page }).include('section[aria-label="Chat to Vula"]').analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`research the web, with sources — ${vp}`, async ({ page }) => {
    await openScreen(page, CHAT, { viewport, theme: "light" });
    const sent = [];
    page.on("request", (r) => { if (r.method() === "POST" && /\/admin\/assistant$/.test(new URL(r.url()).pathname)) sent.push(r.postDataJSON()); });
    const chat = page.getByRole("region", { name: "Chat to Vula" });
    await chat.getByRole("tab", { name: "Ask anything" }).click();
    const toggle = chat.getByRole("button", { name: "Research the web" });
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await expect(chat.getByText(/never names or numbers from your records/)).toBeVisible();
    await chat.getByLabel("Message Vula").fill("What do board bags cost in South Africa?");
    await chat.getByRole("button", { name: "Send" }).click();
    await expect(chat.getByText(/Two suppliers list it/)).toBeVisible();
    await expect(chat.getByText("Researched · 3 pages read")).toBeVisible();
    const sources = chat.getByRole("group", { name: "Sources" });
    const link = sources.getByRole("link", { name: "Board bags — Surf Shop" });
    await expect(link).toHaveAttribute("href", "https://www.surfshop.example/board-bags");
    await expect(link).toHaveAttribute("rel", /noopener/);
    await expect(sources.getByText("surfshop.example", { exact: false })).toBeVisible();
    await expect(sources.getByText(/price-list\.pdf/)).toBeVisible();
    expect(sent[0]).toMatchObject({ message: "What do board bags cost in South Africa?", mode: "research" });
    expect(await serious(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

    await toggle.click();                     // off again: a plain Ask anything question
    await chat.getByLabel("Message Vula").fill("Give me ideas for a winter promotion");
    await chat.getByRole("button", { name: "Send" }).click();
    await expect(chat.getByText(/Five ideas for you/)).toBeVisible();
    expect(sent[1]).toMatchObject({ mode: "open" });
  });
}
