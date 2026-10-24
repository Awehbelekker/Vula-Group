/**
 * sharedFiles.js — files shared into Vula from the phone's Share sheet (2026-10-24).
 *
 * public/sw.js keeps them in this device's Cache Storage ("vula-share") and opens #/share; the
 * Share sheet reads them here and hands them to a screen (the Scanner, Documents), which takes
 * them once. Nothing is uploaded or booked until the person confirms on that screen.
 */
const CACHE = "vula-share";
let pending = null;   // { to, files }

/** The files from the latest share, as File objects (and the copy on the device is removed). */
export async function readSharedFiles() {
  if (typeof caches === "undefined") return [];
  try {
    const cache = await caches.open(CACHE);
    const keys = await cache.keys();
    const files = [];
    for (const req of keys) {
      const res = await cache.match(req);
      if (!res) continue;
      const blob = await res.blob();
      const name = decodeURIComponent(res.headers.get("X-Name") || "shared-file");
      files.push(new File([blob], name, { type: blob.type || res.headers.get("Content-Type") || "" }));
    }
    await caches.delete(CACHE);
    return files;
  } catch {
    return [];
  }
}

/** Give the files to a screen ("scan" or "documents"), which takes them when it opens. */
export function handOff(to, files) { pending = { to, files }; }

/** The files handed to this screen, once. */
export function takeHandOff(to) {
  if (!pending || pending.to !== to) return null;
  const { files } = pending;
  pending = null;
  return files;
}
