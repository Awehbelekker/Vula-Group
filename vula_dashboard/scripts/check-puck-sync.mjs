#!/usr/bin/env node
/**
 * check-puck-sync.mjs — detects drift across the 3 hand-synced Puck block-library copies:
 *   vula_dashboard/src/puck/config.jsx        (source of truth, dashboard editor + public render)
 *   off_the_hook/src/puck.config.tsx          (Next.js storefront copy)
 *   kelp/kelp-board-bags/puck.config.tsx      (Next.js storefront copy)
 *
 * These can't be a single shared package without a monorepo build step (different frameworks —
 * inline styles + plain <a> in the dashboard vs. Tailwind + next/link in the Next.js copies) —
 * see off_the_hook/TEMPLATE.md. This script is the safety net instead: it checks that every
 * block exists in all three with the same field NAMES (not their exact type/implementation,
 * which legitimately differs — e.g. images are a real upload widget in the dashboard, a plain
 * URL field in the Next copies), so adding a field to one copy and forgetting the other two
 * fails a check instead of failing silently in production.
 *
 * Run: node scripts/check-puck-sync.mjs   (from vula_dashboard/)
 * Exit code 0 = in sync, 1 = drift found (prints exactly what differs).
 */
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import path from "path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FILES = {
  dashboard: path.join(__dirname, "../src/puck/config.jsx"),
  off_the_hook: path.join(__dirname, "../../off_the_hook/src/puck.config.tsx"),
  kelp: path.join(__dirname, "../../kelp/kelp-board-bags/puck.config.tsx"),
};

/** Scan a `{ ... }` body (source, with `openBraceIdx` pointing AT the opening `{`) and return
 * the top-level (depth-1) member keys plus the index just past the matching closing `}`. */
function scanTopLevelKeys(source, openBraceIdx) {
  const keys = [];
  let i = openBraceIdx + 1;
  let depth = 1;
  let atMemberStart = true; // true right after `{` or `,` at depth 1
  const keyRe = /^\s*([A-Za-z_]\w*)\s*:/;

  while (i < source.length && depth > 0) {
    if (depth === 1 && atMemberStart) {
      const m = source.slice(i).match(keyRe);
      if (m) {
        keys.push(m[1]);
        i += m[0].length;
        atMemberStart = false;
        continue;
      }
    }
    const ch = source[i];
    if (ch === "{" || ch === "(" || ch === "[") depth += (ch === "{" ? 1 : 0), i += 1, atMemberStart = false;
    else if (ch === "}") { depth -= 1; i += 1; atMemberStart = depth === 1; }
    else if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch; i += 1;
      while (i < source.length && source[i] !== quote) { if (source[i] === "\\") i += 1; i += 1; }
      i += 1;
    } else if (ch === "," && depth === 1) { atMemberStart = true; i += 1; }
    else { i += 1; }
  }
  return { keys, endIndex: i };
}

function extractComponents(source) {
  const compKeyword = "components: {";
  const compStart = source.indexOf(compKeyword);
  if (compStart === -1) throw new Error("couldn't find `components: {`");
  const openBraceIdx = compStart + compKeyword.length - 1;

  const components = {};
  let i = openBraceIdx + 1;
  let depth = 1;
  let atMemberStart = true;
  const nameRe = /^\s*([A-Za-z_]\w*)\s*:\s*\{/;

  while (i < source.length && depth > 0) {
    if (depth === 1 && atMemberStart) {
      const m = source.slice(i).match(nameRe);
      if (m) {
        const blockName = m[1];
        const blockBraceIdx = i + m[0].length - 1;
        // Find `fields: {` within this block's body (search only up to the block's own close).
        const blockScan = scanFieldsOf(source, blockBraceIdx);
        components[blockName] = { fields: blockScan.fields };
        i = blockScan.endIndex;
        atMemberStart = false;
        continue;
      }
    }
    const ch = source[i];
    if (ch === "{") { depth += 1; i += 1; atMemberStart = false; }
    else if (ch === "}") { depth -= 1; i += 1; atMemberStart = depth === 1; }
    else if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch; i += 1;
      while (i < source.length && source[i] !== quote) { if (source[i] === "\\") i += 1; i += 1; }
      i += 1;
    } else if (ch === "," && depth === 1) { atMemberStart = true; i += 1; }
    else { i += 1; }
  }
  return components;
}

/** Given the index of a block's opening `{` (e.g. `Hero: {`), scan its body for a `fields: {`
 * member and extract its top-level keys, returning also the index just past the block's own `}`. */
function scanFieldsOf(source, blockBraceIdx) {
  let i = blockBraceIdx + 1;
  let depth = 1;
  let fields = [];
  while (i < source.length && depth > 0) {
    if (depth === 1 && source.startsWith("fields", i)) {
      const rest = source.slice(i);
      const m = rest.match(/^fields\s*:\s*\{/);
      if (m) {
        const fieldsBraceIdx = i + m[0].length - 1;
        const scanned = scanTopLevelKeys(source, fieldsBraceIdx);
        fields = scanned.keys;
        i = scanned.endIndex;
        continue;
      }
    }
    const ch = source[i];
    if (ch === "{") { depth += 1; i += 1; }
    else if (ch === "}") { depth -= 1; i += 1; }
    else if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch; i += 1;
      while (i < source.length && source[i] !== quote) { if (source[i] === "\\") i += 1; i += 1; }
      i += 1;
    } else { i += 1; }
  }
  return { fields, endIndex: i };
}

let ok = true;
const parsed = {};
for (const [name, filePath] of Object.entries(FILES)) {
  try {
    parsed[name] = extractComponents(readFileSync(filePath, "utf-8"));
  } catch (e) {
    console.error(`✗ Could not parse ${name} (${filePath}): ${e.message}`);
    ok = false;
  }
}
if (!ok) process.exit(1);

const [refName, ...others] = Object.keys(parsed);
const refBlocks = parsed[refName];
const refBlockNames = new Set(Object.keys(refBlocks));

for (const other of others) {
  const otherBlocks = parsed[other];
  const otherBlockNames = new Set(Object.keys(otherBlocks));

  const missingInOther = [...refBlockNames].filter((b) => !otherBlockNames.has(b));
  const extraInOther = [...otherBlockNames].filter((b) => !refBlockNames.has(b));
  if (missingInOther.length) { console.error(`✗ ${other}: missing block(s) present in ${refName}: ${missingInOther.join(", ")}`); ok = false; }
  if (extraInOther.length) { console.error(`✗ ${other}: has extra block(s) not in ${refName}: ${extraInOther.join(", ")}`); ok = false; }

  for (const block of refBlockNames) {
    if (!otherBlocks[block]) continue;
    const refFields = new Set(refBlocks[block].fields);
    const otherFields = new Set(otherBlocks[block].fields);
    const missing = [...refFields].filter((f) => !otherFields.has(f));
    const extra = [...otherFields].filter((f) => !refFields.has(f));
    if (missing.length) { console.error(`✗ ${other}.${block}: missing field(s): ${missing.join(", ")}`); ok = false; }
    if (extra.length) { console.error(`✗ ${other}.${block}: extra field(s) not in ${refName}: ${extra.join(", ")}`); ok = false; }
  }
}

if (ok) {
  console.log(`✓ Puck block library in sync — ${refBlockNames.size} blocks, all field names match across ${Object.keys(FILES).join(", ")}`);
  process.exit(0);
} else {
  console.error("\nFix: bring the listed files' block/field names back in line with the dashboard copy (src/puck/config.jsx is the source of truth).");
  process.exit(1);
}
