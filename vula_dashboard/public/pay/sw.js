/* Vula Pay service worker (scope /pay/).
   - App shell works offline after the first visit (network-first, cache fallback).
   - API calls (/v1/) are never cached: money state must always come from the server.
   - Web Push: show the payment alert; tapping it opens the app. */
const CACHE = "vula-pay-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.add("/pay/")).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/v1/") || url.origin !== self.location.origin) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {}); }
        return res;
      })
      .catch(() => caches.match(req).then((r) => r || (req.mode === "navigate" ? caches.match("/pay/") : Response.error())))
  );
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Vula Pay", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Vula Pay", {
    body: d.body || "", tag: d.tag || "vula-pay", icon: "/icon.svg", badge: "/icon.svg",
    vibrate: [120, 60, 120], data: { url: d.url || "/pay/" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/pay/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if (c.url.includes("/pay/") && "focus" in c) return c.focus(); }
    return self.clients.openWindow(target);
  }));
});
