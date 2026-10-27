// reloadOnce.js (2026-10-27): a screen whose file can't be fetched reloads the page once, then
// hands the error to ErrorBoundary rather than reloading forever.
import { test } from "node:test";
import assert from "node:assert/strict";
import { reloadOnce } from "../../src/lib/reloadOnce.js";

const store = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v) }; };

test("the first failure reloads the page and never shows the error", async () => {
  let reloads = 0;
  const p = reloadOnce(new Error("Failed to fetch dynamically imported module"), { now: 1_000_000, storage: store(), reload: () => reloads++ });
  assert.equal(reloads, 1);
  const settled = await Promise.race([p.then(() => "settled", () => "settled"), new Promise((r) => setTimeout(() => r("pending"), 20))]);
  assert.equal(settled, "pending");
});

test("failing again right after the reload shows the error instead of looping", () => {
  const storage = store();
  let reloads = 0;
  reloadOnce(new Error("x"), { now: 1_000_000, storage, reload: () => reloads++ });
  assert.throws(() => reloadOnce(new Error("still gone"), { now: 1_010_000, storage, reload: () => reloads++ }), /still gone/);
  assert.equal(reloads, 1);
  reloadOnce(new Error("x"), { now: 1_100_000, storage, reload: () => reloads++ });   // much later: may reload again
  assert.equal(reloads, 2);
});

test("with no session storage it shows the error", () => {
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => {} };
  assert.throws(() => reloadOnce(new Error("gone"), { storage: broken, reload: () => assert.fail("no reload") }), /gone/);
});

import { loadScreen } from "../../src/lib/reloadOnce.js";

test("with no signal it waits for the phone to be back online, then loads the screen — no reload", async () => {
  let tries = 0, back;
  const load = async () => { if (++tries === 1) throw new TypeError("Failed to fetch"); return { default: "Screen" }; };
  const p = loadScreen(load, { isOnline: () => false, backOnline: () => new Promise((r) => { back = r; }),
    storage: store(), reload: () => assert.fail("no reload while offline") });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(tries, 1);                                          // waiting, not retrying in a loop
  back();
  assert.deepEqual(await p, { default: "Screen" });
  assert.equal(tries, 2);
});

test("online, a screen whose file is gone reloads the page once", async () => {
  let reloads = 0;
  loadScreen(async () => { throw new TypeError("Failed to fetch"); }, { isOnline: () => true, storage: store(), reload: () => reloads++ });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(reloads, 1);
});
