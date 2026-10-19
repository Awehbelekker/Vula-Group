// dates.js: calendar days in SA time, not UTC (UTC is still yesterday until 02:00 SAST).
import { test } from "node:test";
import assert from "node:assert/strict";
import { isOnSADay, lastDayOfMonth, saDay, todaySA } from "../../src/lib/dates.js";

test("the SA day, not the UTC day, between midnight and 02:00 SAST", () => {
  assert.equal(saDay("2026-10-18T22:30:00Z"), "2026-10-19");   // 00:30 SAST
  assert.equal(saDay("2026-10-19T21:59:00Z"), "2026-10-19");   // 23:59 SAST
  assert.equal(saDay("2026-10-19T22:00:00Z"), "2026-10-20");
});

test("todaySA follows the clock and takes a day offset", () => {
  const real = Date.now;
  Date.now = () => Date.parse("2026-10-18T23:15:00Z");          // 01:15 on the 19th in SA
  try {
    assert.equal(todaySA(), "2026-10-19");
    assert.equal(todaySA(-1), "2026-10-18");
  } finally { Date.now = real; }
});

test("isOnSADay: zoned timestamps convert, bare ones are already SA wall time", () => {
  assert.ok(isOnSADay("2026-10-18T22:30:00Z", "2026-10-19"));
  assert.ok(!isOnSADay("2026-10-18T22:30:00Z", "2026-10-18"));
  assert.ok(isOnSADay("2026-10-19T00:30:00+02:00", "2026-10-19"));
  assert.ok(isOnSADay("2026-10-19T09:00:00", "2026-10-19"));
  assert.ok(!isOnSADay(null, "2026-10-19"));
});

test("lastDayOfMonth, leap years included", () => {
  assert.equal(lastDayOfMonth(2026, 10), "2026-10-31");
  assert.equal(lastDayOfMonth(2026, 11), "2026-11-30");
  assert.equal(lastDayOfMonth(2028, 2), "2028-02-29");
  assert.equal(lastDayOfMonth(2026, 12), "2026-12-31");
});
