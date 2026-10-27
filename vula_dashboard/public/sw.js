/* Vula service worker (2026-10-27, PWA pass 1). Registered from src/main.jsx as /sw.js?v=<build>,
   so every deploy installs a new worker and phones stop running a stale app.

   - Pages (navigations): network first; offline, the last app shell this phone saw, else
     /offline.html. The app then works from what it has and says it's offline.
   - /assets/* (Vite's hashed, never-changing files): cache first.
   - Anything else on this origin: network first, cached copy when offline.
   - Never cached: API calls (other origins, /api/), and anything that isn't a plain GET.
   - Share into Vula: the phone's Share sheet posts to /share-target (see receiveShare).

   On sign-out the app deletes "vula-share" (src/lib/deviceData.js). The shell cache only holds
   the app's own files, never anyone's data. */
const VERSION = new URL(self.location.href).searchParams.get("v") || "dev";
const SHELL = `vula-shell-${VERSION}`;
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icon.svg", "/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL)
      .then((c) => c.addAll(PRECACHE))
      .catch(() => { /* offline page is a nicety: never block the update on it */ })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      // Old app shells go; the share cache stays (it holds what the person just shared).
      Promise.all(keys.filter((k) => k !== SHELL && k !== SHARE_CACHE).map((k) => caches.delete(k)))
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

async function keep(req, res) {
  if (res && res.ok && res.type === "basic") {
    const copy = res.clone();
    caches.open(SHELL).then((c) => c.put(req, copy)).catch(() => {});
  }
  return res;
}

// The dashboard is one page with #/ routes, so its shell is "/". Other pages (a customer's
// receipt at /r/…) are never kept on the phone.
const isShell = (url) => url.pathname === "/" || url.pathname === "/index.html";

async function page(req, url) {
  try {
    const res = await fetch(req);
    if (res.ok && isShell(url)) keep("/", res.clone());              // the app shell for offline
    return res;
  } catch {
    return (isShell(url) && (await caches.match("/"))) || (await caches.match(OFFLINE_URL))
      || new Response("You're offline.", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

async function asset(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  return keep(req, await fetch(req));
}

async function other(req) {
  try {
    return await keep(req, await fetch(req));
  } catch {
    const hit = await caches.match(req);
    if (hit) return hit;
    throw new Error("offline");
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method === "POST" && url.pathname === "/share-target") {
    e.respondWith(receiveShare(req));
    return;
  }
  if (req.method !== "GET") return;
  // Never cache API calls or other sites (the API, Supabase, Meta, fonts) — always the network.
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname.includes("/v1/")) return;
  if (url.pathname.startsWith("/shared/")) return;                    // sharedFiles.js reads the cache itself
  if (req.mode === "navigate") e.respondWith(page(req, url));
  else if (url.pathname.startsWith("/r/")) return;                     // receipts: network only
  else if (url.pathname.startsWith("/assets/")) e.respondWith(asset(req));
  else e.respondWith(other(req));
});
