/* Vula service worker (2026-10-27, PWA pass 1). Registered from src/main.jsx as /sw.js?v=<build>,
   so every deploy installs a new worker and phones stop running a stale app.

   - Pages (navigations): network first; offline, the last app shell this phone saw, else
     /offline.html. The app then works from what it has and says it's offline.
   - /assets/* (Vite's hashed, never-changing files): cache first.
   - Anything else on this origin: network first, cached copy when offline.
   - Never cached: API calls (other origins, /api/), and anything that isn't a plain GET.
   - Share into Vula: the phone's Share sheet posts to /share-target (see receiveShare).

   On sign-out the app deletes "vula-share" (src/lib/deviceData.js). The shell cache only holds
   the app's own files, never anyone's data.

   Every screen opens offline (2026-10-27, PWA pass 5): on install the worker keeps the app shell
   and every file the dashboard can load (precache.json, written by the build — precache.js),
   not only the screens opened since the last update. The previous build's files stay too, until
   the next one: an app that was already open keeps running that build until it's reloaded. */
const VERSION = new URL(self.location.href).searchParams.get("v") || "dev";
const SHELL = `vula-shell-${VERSION}`;
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icon.svg", "/icon-192.png"];

// This build's file list (precache.json); none in development, or when it can't be fetched.
async function buildFiles() {
  try {
    const res = await fetch(`/precache.json?v=${encodeURIComponent(VERSION)}`, { cache: "no-store" });
    const list = res.ok ? await res.json() : null;
    return Array.isArray(list?.files) ? list.files.filter((f) => typeof f === "string" && f.startsWith("/assets/")) : [];
  } catch { return []; }
}

// Each file on its own: one that fails is fetched the first time it's used instead, as before.
async function keepAll(cache, urls) {
  await Promise.all(urls.map((u) => fetch(new Request(u, { cache: "reload" }))
    .then((res) => (res.ok ? cache.put(u, res) : null)).catch(() => null)));
}

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    try {
      const cache = await caches.open(SHELL);
      await keepAll(cache, [...PRECACHE, "/", ...(await buildFiles())]);
    } catch { /* never block the update on it */ }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      // The share cache stays (it holds what the person just shared), and so does the previous
      // build's shell (an open app may still be running it); older shells go. Keys come back
      // oldest first.
      const shells = keys.filter((k) => k.startsWith("vula-shell-") && k !== SHELL);
      const previous = shells[shells.length - 1];
      return Promise.all(keys.filter((k) => k !== SHELL && k !== SHARE_CACHE && k !== previous).map((k) => caches.delete(k)));
    }).then(() => self.clients.claim())
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
    // This build's shell first: the previous build's is kept too, and caches.match() looks there first.
    const shell = isShell(url) && ((await (await caches.open(SHELL)).match("/")) || (await caches.match("/")));
    return shell || (await caches.match(OFFLINE_URL))
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

// Notifications (2026-10-27, PWA pass 3): an alert this person also gets on WhatsApp
// (vula_mind/vula/dash_push.py). A tap opens the page it's about, in the open app when there is one.
// With Vula open on screen the notification is silent and the app chimes instead (one sound, not
// two: src/lib/alertChime.js); otherwise the phone's own notification sound plays. renotify: a
// second alert of the same kind (two customers waiting) sounds again instead of quietly
// replacing the first.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Vula", body: e.data ? e.data.text() : "" }; }
  e.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const onScreen = windows.filter((c) => c.visibilityState === "visible" && !new URL(c.url).pathname.startsWith("/pay/"));
    await self.registration.showNotification(d.title || "Vula", {
      body: d.body || "", tag: d.tag || "vula", renotify: true, silent: onScreen.length > 0,
      icon: "/icon-192.png", badge: "/icon-192.png", vibrate: [120, 60, 120], data: { url: d.url || "/" },
    });
    onScreen.forEach((c) => c.postMessage({ type: "vula-alert", title: d.title || "Vula", tag: d.tag || "vula" }));
    // A dot on the app's icon until Vula is opened (src/lib/appBadge.js sets the real number then).
    if (!onScreen.length && self.navigator.setAppBadge) await self.navigator.setAppBadge().catch(() => {});
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    const open = list.find((c) => new URL(c.url).origin === self.location.origin && !new URL(c.url).pathname.startsWith("/pay/"));
    if (open) return open.focus().then((c) => (c && "navigate" in c ? c.navigate(target) : c));
    return self.clients.openWindow(target);
  }));
});
