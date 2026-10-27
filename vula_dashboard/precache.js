// precache.js — the list of files the installed app keeps on the phone (2026-10-27, PWA pass 5).
// Screens that load only when first opened (Share, Pages, the stock-count barcode reader, the
// map in Settings) used not to open with no signal unless they'd been opened since the last
// update. The build now writes precache.json: every file the dashboard (index.html) can load,
// found by following its imports — never the coach app (/pay/) or a customer's receipt (/r/).
// public/sw.js fetches it when it installs and keeps all of them.

/** The files `entry` can load: its own chunk, every chunk it imports or loads later, and their CSS. */
export function filesFor(bundle, entry = "main") {
  const chunks = Object.values(bundle).filter((c) => c.type === "chunk");
  const start = chunks.find((c) => c.isEntry && c.name === entry);
  if (!start) return [];
  const byFile = new Map(chunks.map((c) => [c.fileName, c]));
  const seen = new Set();
  const files = new Set();
  const walk = (chunk) => {
    if (!chunk || seen.has(chunk.fileName)) return;
    seen.add(chunk.fileName);
    files.add(chunk.fileName);
    for (const css of chunk.viteMetadata?.importedCss || []) files.add(css);
    for (const f of [...(chunk.imports || []), ...(chunk.dynamicImports || [])]) walk(byFile.get(f));
  };
  walk(start);
  return [...files].sort();
}

/** The Vite plugin: writes precache.json next to index.html. The imports are followed before Vite
 *  drops a CSS-only chunk (the map's stylesheet keeps its file, its empty JS chunk goes), and only
 *  what was written to disk is listed. */
export function precacheList(build) {
  let files = [];
  return {
    name: "vula-precache-list",
    apply: "build",
    generateBundle(_options, bundle) { files = filesFor(bundle); },
    async writeBundle(options) {
      const { existsSync } = await import("node:fs");
      const { writeFile } = await import("node:fs/promises");
      const { join } = await import("node:path");
      const kept = files.filter((f) => existsSync(join(options.dir, f)));
      await writeFile(join(options.dir, "precache.json"), JSON.stringify({ build, files: kept.map((f) => `/${f}`) }));
    },
  };
}
