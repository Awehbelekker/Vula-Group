// check-size.mjs — the speed budget (2026-10-27, PWA pass 6). Run after `npm run build`.
//
// Shop owners open Vula on a phone, often on mobile data. Two numbers, gzipped (about what a phone
// downloads; Vercel's brotli is a little smaller):
//   first_load  — what opening the app downloads before anything shows: index.html's own scripts
//                 and stylesheets (the main bundle and what it imports straight away).
//   everything  — every file the installed app keeps for offline use (dist/precache.json, the
//                 service worker downloads these once per update — precache.js).
// The build fails when either goes over scripts/size-budget.json. Raise a budget only on purpose,
// in the same change that needs it, and say why in the commit; better, load the new screen lazily.
import { readFileSync, existsSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const budget = JSON.parse(readFileSync(join(root, "scripts/size-budget.json"), "utf8"));
const kb = (n) => `${(n / 1024).toFixed(0)} KB`;
const gz = (f) => gzipSync(readFileSync(join(dist, f))).length;

if (!existsSync(join(dist, "index.html"))) {
  console.error("check-size: no dist/ — run `npm run build` first.");
  process.exit(1);
}
const html = readFileSync(join(dist, "index.html"), "utf8");
const firstFiles = [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]);
const allFiles = JSON.parse(readFileSync(join(dist, "precache.json"), "utf8")).files.map((f) => f.replace(/^\//, ""));

const sizes = new Map([...new Set([...firstFiles, ...allFiles])].map((f) => [f, gz(f)]));
const total = (files) => [...new Set(files)].reduce((a, f) => a + sizes.get(f), 0);
const result = { first_load: total(firstFiles), everything: total(allFiles) };

let failed = false;
for (const [k, used] of Object.entries(result)) {
  const limit = budget[k] * 1024;
  const over = used > limit;
  failed ||= over;
  console.log(`${over ? "OVER " : "ok   "} ${k.padEnd(10)} ${kb(used).padStart(8)} of ${kb(limit)}${over ? `  (+${kb(used - limit)})` : ""}`);
}
console.log("\nlargest files (gzipped):");
[...sizes].sort((a, b) => b[1] - a[1]).slice(0, 6).forEach(([f, n]) => console.log(`  ${kb(n).padStart(8)}  ${f}${firstFiles.includes(f) ? "  (first load)" : ""}`));
if (failed) {
  console.error("\nOver the speed budget (scripts/size-budget.json). Load the new code lazily (React.lazy / import()), or raise the budget on purpose and say why.");
  process.exit(1);
}
