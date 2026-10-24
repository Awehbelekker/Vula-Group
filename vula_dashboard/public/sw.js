/* Vula Commerce service worker — minimal network-first with cache fallback.
   Gives installability (a fetch handler) + basic offline app shell. */
const CACHE = "vula-shell-v1";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Share into Vula (2026-10-24): the phone's Share sheet posts photos / PDFs here (the manifest's
// share_target). They're kept in this device's Cache Storage — never sent anywhere yet — and the
// app opens at #/share, where the person picks what to do with them (src/lib/sharedFiles.js).
const SHARE_CACHE = "vula-share";
async function receiveShare(request) {
  try {
    const form = await request.formData();
    const files = form.getAll("files").filter((f) => f && typeof f === "object" && f.size);
    const cache = await caches.open(SHARE_CACHE);
    for (const k of await cache.keys()) await cache.delete(k);       // only the latest share
    await Promise.all(files.slice(0, 10).map((f, i) => cache.put(`/shared/${i}`, new Response(f, {
      headers: { "Content-Type": f.type || "application/octet-stream", "X-Name": encodeURIComponent(f.name || `shared-${i + 1}`) },
    }))));
  } catch { /* nothing kept: the Share screen says so */ }
  return Response.redirect("/#/share", 303);
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method === "POST" && new URL(req.url).pathname === "/share-target") {
    e.respondWith(receiveShare(req));
    return;
  }
  if (req.method !== "GET") return;
  // Never cache API calls — always go to network.
  if (req.url.includes("/v1/") || req.url.includes("supabase")) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((r) => r || caches.match("/")))
  );
});
