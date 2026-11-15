// assistant-sheets.spec.js — spreadsheets out of the chat (13 Nov 2026): a sheet Vula made shows
// as a Download button and its link is clickable; a reply with a table has "Download as
// spreadsheet", which posts the reply's id and saves the file. Phone, tablet and desktop: axe
// clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as F from "./fixtures/demo.js";
import { VIEWPORTS, openScreen } from "./screens.js";

F.assistantChat.openChat = true;          // this file only: each spec file runs in its own worker
const CHAT = { name: "chat", role: "owner", nav: "Assistant", tab: "Chat", ready: "What were today's sales?" };
const serious = async (page) => (await new AxeBuilder({ page }).include('section[aria-label="Chat to Vula"]').analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`a made sheet downloads, and a table becomes a spreadsheet — ${vp}`, async ({ page }) => {
    await openScreen(page, CHAT, { viewport, theme: "light" });
    const sheets = [];
    page.on("request", (r) => { if (r.method() === "POST" && /\/assistant\/sheet$/.test(new URL(r.url()).pathname)) sheets.push(r.postDataJSON()); });
    const chat = page.getByRole("region", { name: "Chat to Vula" });

    await chat.getByLabel("Message Vula").fill("Make me a spreadsheet of my products with prices");
    await chat.getByRole("button", { name: "Send" }).click();
    const files = chat.getByRole("group", { name: "Files from Vula" });
    const dl = files.getByRole("link", { name: /Download Price_list_2026-11-13\.xlsx/ });
    await expect(dl).toHaveAttribute("href", "https://files.vula.test/sheets/Price_list.xlsx");
    await expect(dl).toHaveAttribute("rel", /noopener/);
    await expect(chat.getByRole("link", { name: "files.vula.test" })).toBeVisible();   // the link in the text

    await chat.getByRole("tab", { name: "Ask anything" }).click();
    await chat.getByLabel("Message Vula").fill("Compare board bag suppliers as a table");
    await chat.getByRole("button", { name: "Send" }).click();
    const make = chat.getByRole("button", { name: "Download as spreadsheet" });
    await expect(make).toBeVisible();
    expect(await serious(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    const download = page.waitForEvent("download");
    await make.click();
    expect((await download).suggestedFilename()).toBe("Board_bags_2026-11-13.xlsx");
    expect(sheets[0]).toMatchObject({ message_id: "msg-table" });
  });
}
