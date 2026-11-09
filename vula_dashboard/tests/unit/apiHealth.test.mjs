// apiHealth (lib/apiHealth.js): the "Vula can't be reached" banner (9 Nov 2026 audit).
// Run: npm run test:unit
import { test } from "node:test";
import assert from "node:assert/strict";
import { isDownOutcome, reportApiOutcome, API_HEALTH_EVENT } from "../../src/lib/apiHealth.js";

test("no answer while online, or a gateway error, means the server is down", () => {
  assert.equal(isDownOutcome({ networkError: true, online: true }), true);
  for (const status of [502, 503, 504]) assert.equal(isDownOutcome({ status }), true);
});

test("offline is the offline banner's job, and ordinary answers are not an outage", () => {
  assert.equal(isDownOutcome({ networkError: true, online: false }), false);
  for (const status of [200, 401, 404, 409, 422, 500]) assert.equal(isDownOutcome({ status }), false);
});

test("the app is told only when it changes", () => {
  const seen = [];
  const target = { dispatchEvent: (e) => seen.push([e.type, e.detail.down]) };
  reportApiOutcome({ status: 503 }, target);
  reportApiOutcome({ status: 503 }, target);
  reportApiOutcome({ networkError: true }, target);
  reportApiOutcome({ status: 200 }, target);
  reportApiOutcome({ status: 200 }, target);
  assert.deepEqual(seen, [[API_HEALTH_EVENT, true], [API_HEALTH_EVENT, false]]);
});
