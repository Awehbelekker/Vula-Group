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

function worker({ version = "b2" } = {}) {
  const on = {};
  const net = { online: true, calls: [] };
  const stores = new Map();
  const keyOf = (r) => new URL(typeof r === "string" ? r : r.url, ORIGIN).href;
  const fetchFn = async (req) => {
    const url = keyOf(req);
    net.calls.push(new URL(url).pathname);
    if (!net.online) throw new TypeError("Failed to fetch");
    return new Basic(`net:${new URL(url).pathname}`, { status: 200 });
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
    skipWaiting: async () => {}, clients: { claim: async () => {} },
  };
  vm.runInNewContext(CODE, { self, caches, fetch: fetchFn, Response, URL, console });

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
  return { net, stores, run, get };
}

test("installs with this build's name and keeps the offline page", async () => {
  const w = worker({ version: "b2" });
  await w.run("install");
  assert.deepEqual([...w.stores.keys()], ["vula-shell-b2"]);
  assert.ok(w.stores.get("vula-shell-b2").has(`${ORIGIN}/offline.html`));
});

test("a new build drops the old app shell but keeps what was shared into Vula", async () => {
  const w = worker({ version: "b2" });
  w.stores.set("vula-shell-b1", new Map()); w.stores.set("vula-share", new Map()); w.stores.set("vula-shell-v1", new Map());
  await w.run("install");
  await w.run("activate");
  assert.deepEqual([...w.stores.keys()].sort(), ["vula-share", "vula-shell-b2"]);
});

test("online, the app comes from the network and is kept for later", async () => {
  const w = worker();
  await w.run("install");
  assert.equal(await w.get("/", { mode: "navigate" }), "net:/");
  assert.equal(w.stores.get("vula-shell-b2").get(`${ORIGIN}/`), "net:/");
});

test("offline, the app opens from the copy it kept; with no copy, the offline page", async () => {
  const w = worker();
  await w.run("install");
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
