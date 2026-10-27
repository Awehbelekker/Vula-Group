// precache.js (2026-10-27, PWA pass 5): the files the installed app keeps on the phone are every
// file the dashboard can load — followed through its imports and later-loaded screens — and
// nothing only the coach app or a customer's receipt uses.
import { test } from "node:test";
import assert from "node:assert/strict";
import { filesFor } from "../../precache.js";

const chunk = (fileName, more = {}) => ({ type: "chunk", fileName, imports: [], dynamicImports: [], ...more });

const BUNDLE = {
  main: chunk("assets/main-a.js", { isEntry: true, name: "main", imports: ["assets/client-b.js"],
    dynamicImports: ["assets/ShareSheet-c.js", "assets/leaflet-d.js"], viteMetadata: { importedCss: new Set(["assets/main-a.css"]) } }),
  client: chunk("assets/client-b.js"),
  share: chunk("assets/ShareSheet-c.js", { viteMetadata: { importedCss: new Set(["assets/ShareSheet-c.css"]) } }),
  leaflet: chunk("assets/leaflet-d.js", { dynamicImports: ["assets/leaflet-src-e.js"], viteMetadata: { importedCss: new Set(["assets/leaflet-d.css"]) } }),
  leafletSrc: chunk("assets/leaflet-src-e.js", { dynamicImports: ["assets/leaflet-d.js"] }),   // a cycle
  pay: chunk("assets/pay-f.js", { isEntry: true, name: "pay", imports: ["assets/client-b.js", "assets/coach-g.js"] }),
  coach: chunk("assets/coach-g.js"),
  receipt: chunk("assets/receipt-h.js", { isEntry: true, name: "receipt" }),
  css: { type: "asset", fileName: "assets/main-a.css" },
};

test("every file the dashboard can load, screens opened later included", () => {
  assert.deepEqual(filesFor(BUNDLE), [
    "assets/ShareSheet-c.css", "assets/ShareSheet-c.js", "assets/client-b.js", "assets/leaflet-d.css",
    "assets/leaflet-d.js", "assets/leaflet-src-e.js", "assets/main-a.css", "assets/main-a.js",
  ]);
});

test("nothing only the coach app or a customer's receipt uses", () => {
  const files = filesFor(BUNDLE);
  assert.ok(!files.some((f) => /pay-|coach-|receipt-/.test(f)));
});

test("no dashboard entry, no list", () => {
  assert.deepEqual(filesFor({ pay: BUNDLE.pay }), []);
});
