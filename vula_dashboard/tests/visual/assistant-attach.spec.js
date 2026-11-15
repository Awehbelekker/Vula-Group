// assistant-attach.spec.js — files attached in the dashboard chat (13 Nov 2026): 📎 picks a file,
// it shows as a chip, goes with the next message as an attachment id, can be removed or kept in
// the business's documents, and the sent message names it. Phone, tablet and desktop: axe
// clean, no sideways scroll.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import * as F from "./fixtures/demo.js";
import { VIEWPORTS, openScreen } from "./screens.js";

F.assistantChat.openChat = true;          // this file only: each spec file runs in its own worker
const CHAT = { name: "chat", role: "owner", nav: "Assistant", tab: "Chat", ready: "What were today's sales?" };
const SHEET = { name: "prices.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("x") };
const serious = async (page) => (await new AxeBuilder({ page }).include('section[aria-label="Chat to Vula"]').analyze()).violations
  .filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id);

for (const [vp, viewport] of Object.entries(VIEWPORTS)) {
  test(`attach a sheet and ask about it — ${vp}`, async ({ page }) => {
    await openScreen(page, CHAT, { viewport, theme: "light" });
    const sent = [];
    page.on("request", (r) => { if (r.method() === "POST" && /\/admin\/assistant$/.test(new URL(r.url()).pathname)) sent.push(r.postDataJSON()); });
    const chat = page.getByRole("region", { name: "Chat to Vula" });
    await chat.getByRole("tab", { name: "Ask anything" }).click();
    const chooser = page.waitForEvent("filechooser");
    await chat.getByRole("button", { name: "Attach a file" }).click();
    await (await chooser).setFiles(SHEET);
    const files = chat.getByRole("group", { name: "Attached files" });
    await expect(files.getByText("prices.xlsx")).toBeVisible();
    expect(await serious(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await chat.getByLabel("Message Vula").fill("What's the price of the 12' bag?");
    await chat.getByRole("button", { name: "Send" }).click();
    await expect(chat.getByText("📎 prices.xlsx")).toBeVisible();
    await expect(files).toHaveCount(0);                                        // sent, so cleared
    expect(sent[0]).toMatchObject({ message: "What's the price of the 12' bag?", mode: "open", attachments: [`${"a".repeat(31)}0`] });
  });
}

test("a file can be kept in the documents or removed before sending", async ({ page }) => {
  await openScreen(page, CHAT, { viewport: VIEWPORTS.desktop, theme: "light" });
  const kept = [];
  page.on("request", (r) => { if (r.method() === "POST" && /\/attach\/[^/]+\/file$/.test(new URL(r.url()).pathname)) kept.push(r.url()); });
  const chat = page.getByRole("region", { name: "Chat to Vula" });
  const chooser = page.waitForEvent("filechooser");
  await chat.getByRole("button", { name: "Attach a file" }).click();
  await (await chooser).setFiles([SHEET, { ...SHEET, name: "terms.pdf", mimeType: "application/pdf" }]);
  const files = chat.getByRole("group", { name: "Attached files" });
  await files.getByRole("button", { name: "Keep in our documents" }).first().click();
  await expect(files.getByText("In your documents")).toBeVisible();
  expect(kept).toHaveLength(1);
  await files.getByRole("button", { name: "Remove terms.pdf" }).click();
  await expect(files.getByText("terms.pdf")).toHaveCount(0);
  await expect(files.getByText("prices.xlsx")).toBeVisible();
});
