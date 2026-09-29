// check-design-tokens.mjs — keep every page on the shared design tokens (2026-09-29).
// Fails CI when a file gains raw hex colours or a hardcoded font family beyond its recorded
// baseline (scripts/design-token-baseline.json), or a new file has any. The baseline only goes
// DOWN: run with --update after migrating a page to lower its allowance.
// theme/ (where the tokens live) is exempt.
import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("../src/", import.meta.url).pathname;
const BASELINE = new URL("./design-token-baseline.json", import.meta.url).pathname;
const HEX = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b/g;
const FONT = /fontFamily\s*:\s*['"](?!var\()/g;

function files(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return n === "theme" || n === "__preview" ? [] : files(p);
    return /\.(jsx?|tsx?)$/.test(n) ? [p] : [];
  });
}
const counts = {};
for (const f of files(ROOT)) {
  const src = readFileSync(f, "utf8");
  const n = (src.match(HEX) || []).length + (src.match(FONT) || []).length;
  if (n) counts[relative(ROOT, f)] = n;
}
if (process.argv.includes("--update")) {
  writeFileSync(BASELINE, JSON.stringify(counts, null, 2) + "\n");
  console.log(`baseline written: ${Object.keys(counts).length} files`);
  process.exit(0);
}
const base = JSON.parse(readFileSync(BASELINE, "utf8"));
const over = Object.entries(counts).filter(([f, n]) => n > (base[f] || 0));
if (over.length) {
  console.error("Hardcoded colours/fonts added — use the tokens (var(--accent), var(--muted), var(--ok)…, T.* in theme/tokens.js):");
  for (const [f, n] of over) console.error(`  ${f}: ${n} (allowed ${base[f] || 0})`);
  process.exit(1);
}
const total = Object.values(counts).reduce((a, b) => a + b, 0);
console.log(`design tokens OK — ${total} hardcoded values left across ${Object.keys(counts).length} files`);
