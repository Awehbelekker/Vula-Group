// assignment.test.mjs — which business a login enters (src/lib/assignment.js, 19 Nov 2026).
import { test } from "node:test";
import assert from "node:assert/strict";
import { pickAssignment } from "../../src/lib/assignment.js";

test("master, then owner, then staff, then accountant", () => {
  assert.deepEqual(pickAssignment([{ tenant_id: "b", role: "staff" }, { tenant_id: "a", role: "owner" }], null),
    { tenant_id: "a", role: "owner" });
  assert.equal(pickAssignment([{ tenant_id: "x", role: "staff" }, { tenant_id: "master", role: "master" }], null).role, "master");
  assert.equal(pickAssignment([], null), null);
  assert.equal(pickAssignment([{ tenant_id: "x", role: "weird" }], null), null);
});

test("an accountant opens the client they last had open, else the first", () => {
  const rows = [{ tenant_id: "digg", role: "accountant" }, { tenant_id: "atlas", role: "accountant" }];
  assert.equal(pickAssignment(rows, null).tenant_id, "atlas");
  assert.equal(pickAssignment(rows, "digg").tenant_id, "digg");
  assert.equal(pickAssignment(rows, "gone").tenant_id, "atlas");
});
