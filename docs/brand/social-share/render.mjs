import { chromium } from "../../../vula_dashboard/node_modules/playwright/index.mjs";
const dir = new URL(".", import.meta.url).pathname.replace(/\/$/, "");
const b = await chromium.launch();
for (const [scale, name] of [[1, "vula-social-share-green-1200x630.png"], [2, "vula-social-share-green-2400x1260.png"]]) {
  const p = await b.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: scale });
  await p.goto(`file://${dir}/share.html`); await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(200);
  await p.screenshot({ path: `${dir}/${name}`, clip: { x: 0, y: 0, width: 1200, height: 630 } });
}
await b.close();
