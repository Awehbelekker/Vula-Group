/* FlowCrew customer app service worker (scope /flow/).
   - The app opens with no signal after the first visit (network first, the kept copy when offline).
   - API calls (another origin, or /v1/ and /api/ here) are never kept: prices, the forecast and a
     request's status always come from the server. */
const CACHE = "flowcrew-app-v1";
const SHELL = "/flow/";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll([SHELL, "/flow/icon-192.png", "/partners/flowcrew-mark.png"])).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("flowcrew-app-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/v1/") || url.pathname.startsWith("/api/")) return;
  const ours = url.pathname.startsWith("/flow/") || url.pathname.startsWith("/assets/") || url.pathname.startsWith("/partners/");
  if (!ours) return;
  e.respondWith(fetch(req)
    .then((res) => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req.mode === "navigate" ? SHELL : req, copy)).catch(() => {}); } return res; })
    .catch(() => caches.match(req.mode === "navigate" ? SHELL : req).then((r) => r || Response.error())));
});
