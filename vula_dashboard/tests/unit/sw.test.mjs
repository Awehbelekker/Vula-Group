// public/sw.js (2026-10-27, PWA pass 1): what the service worker does with and without signal.
// It runs here against a fake network and Cache Storage: a browser test can't take a service
// worker's own requests offline.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ORIGIN = "https://app.vula.test";
const CODE = readFileSync(new URL("../../public/sw.js", import.meta.url), "utf8");
const tick = () => new Promise((r) => setTimeout(r, 10));

// A same-origin response, as a browser gives the worker (Node's Response is never "basic").
class Basic extends Response {
  get type() { return "basic"; }
  clone() { return Object.setPrototypeOf(super.clone(), Basic.prototype); }
}

// A request the worker builds itself (new Request("/assets/…", {cache: "reload"})).
class Req {
  constructor(url, opts = {}) { this.url = new URL(url, ORIGIN).href; this.cache = opts.cache; }
}

// precache: this build's file list (precache.json); fail: paths the network answers with a 500.
function worker({ version = "b2", windows = [], precache = null, fail = [] } = {}) {
  const on = {};
  const shown = [], badges = [];
  const net = { online: true, calls: [] };
  const stores = new Map();
  const keyOf = (r) => new URL(typeof r === "string" ? r : r.url, ORIGIN).href;
  const fetchFn = async (req) => {
    const url = keyOf(req);
    net.calls.push(new URL(url).pathname);
    if (!net.online) throw new TypeError("Failed to fetch");
    const path = new URL(url).pathname;
    if (path === "/precache.json") return precache ? new Basic(JSON.stringify(precache), { status: 200 }) : new Basic("not here", { status: 404 });
    if (fail.includes(path)) return new Basic("server error", { status: 500 });
    return new Basic(`net:${path}`, { status: 200 });
  };
  const cache = (m) => ({
    put: async (r, res) => { m.set(keyOf(r), await res.text()); },
    match: async (r) => (m.has(keyOf(r)) ? new Response(m.get(keyOf(r))) : undefined),
    delete: async (r) => m.delete(keyOf(r)),
    keys: async () => [...m.keys()].map((url) => ({ url })),
    addAll: async (urls) => { for (const u of urls) m.set(keyOf(u), await (await fetchFn({ url: keyOf(u) })).text()); },
  });
  const caches = {
    open: async (n) => { if (!stores.has(n)) stores.set(n, new Map()); return cache(stores.get(n)); },
    keys: async () => [...stores.keys()],
    delete: async (n) => stores.delete(n),
    has: async (n) => stores.has(n),
    match: async (r) => { for (const m of stores.values()) if (m.has(keyOf(r))) return new Response(m.get(keyOf(r))); },
  };
  const self = {
    location: new URL(`${ORIGIN}/sw.js?v=${version}`),
    addEventListener: (type, fn) => { on[type] = fn; },
    skipWaiting: async () => {},
    clients: { claim: async () => {}, matchAll: async () => windows },
    registration: { showNotification: async (title, opts) => { shown.push({ title, ...opts }); } },
    navigator: { setAppBadge: async () => { badges.push("dot"); } },
  };
  vm.runInNewContext(CODE, { self, caches, fetch: fetchFn, Request: Req, Response, URL, console });

  const run = async (type) => { let p; on[type]({ waitUntil: (x) => { p = x; } }); await p; };
  // A request as the browser hands it over; null when the worker leaves it to the network.
  const get = async (path, { mode = "cors", method = "GET", origin = ORIGIN } = {}) => {
    let p = null;
    on.fetch({ request: { url: `${origin}${path}`, method, mode }, respondWith: (x) => { p = x; } });
    if (!p) return null;
    const res = await p;
    await tick();
    return res.text();
  };
  // A push from the server: its payload, as the browser hands it to the worker.
  const push = async (payload) => {
    let p;
    on.push({ data: { json: () => payload, text: () => JSON.stringify(payload) }, waitUntil: (x) => { p = x; } });
    await p;
  };
  return { net, stores, run, get, push, shown, badges };
}

test("installs with this build's name and keeps the offline page", async () => {
  const w = worker({ version: "b2" });
  await w.run("install");
  assert.deepEqual([...w.stores.keys()], ["vula-shell-b2"]);
  assert.ok(w.stores.get("vula-shell-b2").has(`${ORIGIN}/offline.html`));
});

test("a new build keeps the previous build's files (an open app may still run it), not older ones, and what was shared", async () => {
  const w = worker({ version: "b2" });
  w.stores.set("vula-shell-b0", new Map()); w.stores.set("vula-share", new Map()); w.stores.set("vula-shell-b1", new Map());
  await w.run("install");
  await w.run("activate");
  assert.deepEqual([...w.stores.keys()].sort(), ["vula-share", "vula-shell-b1", "vula-shell-b2"]);
});

test("installing keeps every file the dashboard can load, so every screen opens offline", async () => {
  const files = ["/assets/main-a1.js", "/assets/main-a1.css", "/assets/ShareSheet-c3.js", "/assets/leaflet-src-d4.js"];
  const w = worker({ precache: { build: "b2", files: [...files, "https://evil.test/x.js", "/r/secret"] } });
  await w.run("install");
  const kept = w.stores.get("vula-shell-b2");
  for (const f of files) assert.equal(kept.get(`${ORIGIN}${f}`), `net:${f}`);
  assert.ok(kept.has(`${ORIGIN}/`));                                         // the app shell too
  assert.ok(![...kept.keys()].some((k) => k.includes("evil") || k.includes("/r/")));   // only the build's own files
  w.net.online = false;
  assert.equal(await w.get("/assets/ShareSheet-c3.js"), "net:/assets/ShareSheet-c3.js");   // never opened, still there
});

test("a file that won't download doesn't stop the update; with no list the update still installs", async () => {
  const w = worker({ precache: { files: ["/assets/a.js", "/assets/b.js"] }, fail: ["/assets/b.js"] });
  await w.run("install");
  assert.ok(w.stores.get("vula-shell-b2").has(`${ORIGIN}/assets/a.js`));
  assert.ok(!w.stores.get("vula-shell-b2").has(`${ORIGIN}/assets/b.js`));   // fetched when first used instead
  const bare = worker();                                                     // development: no precache.json
  await bare.run("install");
  assert.ok(bare.stores.get("vula-shell-b2").has(`${ORIGIN}/offline.html`));
});

test("offline, the app opens as this build, not the previous one kept beside it", async () => {
  const w = worker({ version: "b2" });
  w.stores.set("vula-shell-b1", new Map([[`${ORIGIN}/`, "old shell"]]));
  await w.run("install");
  await w.run("activate");
  w.net.online = false;
  assert.equal(await w.get("/", { mode: "navigate" }), "net:/");
});

test("online, the app comes from the network and is kept for later", async () => {
  const w = worker();
  await w.run("install");
  assert.equal(await w.get("/", { mode: "navigate" }), "net:/");
  assert.equal(w.stores.get("vula-shell-b2").get(`${ORIGIN}/`), "net:/");
});

test("offline, the app opens from the copy it kept; with no copy, the offline page", async () => {
  const fail = ["/"];                                                         // the shell didn't download at install
  const w = worker({ fail });
  await w.run("install");
  fail.length = 0;
  w.net.online = false;
  assert.equal(await w.get("/", { mode: "navigate" }), "net:/offline.html");
  w.net.online = true;
  await w.get("/", { mode: "navigate" });
  w.net.online = false;
  assert.equal(await w.get("/", { mode: "navigate" }), "net:/");
});

test("a customer's receipt is never kept, and offline it gets the offline page, not the dashboard", async () => {
  const w = worker();
  await w.run("install");
  await w.get("/", { mode: "navigate" });
  assert.equal(await w.get("/r/tok123", { mode: "navigate" }), "net:/r/tok123");
  assert.ok(!w.stores.get("vula-shell-b2").has(`${ORIGIN}/r/tok123`));
  assert.equal(w.stores.get("vula-shell-b2").get(`${ORIGIN}/`), "net:/");         // the shell is untouched
  w.net.online = false;
  assert.equal(await w.get("/r/tok123", { mode: "navigate" }), "net:/offline.html");
  assert.equal(await w.get("/r/tok123/slip.png"), null);
});

test("built files are cached once and then come from the phone", async () => {
  const w = worker();
  await w.run("install");
  assert.equal(await w.get("/assets/main-abc.js"), "net:/assets/main-abc.js");
  w.net.online = false;
  assert.equal(await w.get("/assets/main-abc.js"), "net:/assets/main-abc.js");
  assert.equal(w.net.calls.filter((c) => c === "/assets/main-abc.js").length, 1);
});

test("API calls, other sites and anything but a plain GET always go to the network", async () => {
  const w = worker();
  await w.run("install");
  assert.equal(await w.get("/api/v1/commerce/x/admin/orders"), null);
  assert.equal(await w.get("/v1/whatsapp/connect/status/x"), null);
  assert.equal(await w.get("/v1/orders", { origin: "https://api.vula.test" }), null);
  assert.equal(await w.get("/", { method: "POST" }), null);
  assert.equal(await w.get("/shared/0"), null);
});


test("an alert sounds on the phone, and a second of the same kind sounds again", async () => {
  const w = worker();
  await w.push({ title: "A customer is waiting", body: "Thandi asked about delivery", tag: "customer_unanswered", url: "/#/go/inbox" });
  assert.equal(w.shown.length, 1);
  assert.equal(w.shown[0].title, "A customer is waiting");
  assert.equal(w.shown[0].silent, false);
  assert.equal(w.shown[0].renotify, true);
  assert.equal(w.shown[0].data.url, "/#/go/inbox");
  assert.deepEqual(w.badges, ["dot"]);                          // a dot on the app's icon till it's opened
});

test("with Vula open on screen the notification is silent and the app is told to chime", async () => {
  const told = [];
  const open = { url: `${ORIGIN}/#/go/orders`, visibilityState: "visible", postMessage: (m) => told.push(m) };
  const hidden = { url: `${ORIGIN}/`, visibilityState: "hidden", postMessage: (m) => told.push({ hidden: m }) };
  const w = worker({ windows: [open, hidden] });
  await w.push({ title: "New paid order", body: "OTH-12 · R318.40", tag: "order-OTH-12" });
  assert.equal(w.shown[0].silent, true);
  assert.deepEqual(JSON.parse(JSON.stringify(told)), [{ type: "vula-alert", title: "New paid order", tag: "order-OTH-12" }]);
  assert.deepEqual(w.badges, []);                               // they're looking at it: no dot
});
