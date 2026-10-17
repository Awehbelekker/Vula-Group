// formatMoney (lib/money.js): integer cents → "R 318 400" (2026-10-17, UX redesign B4).
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatMoney } from "../../src/lib/money.js";

const S = " "; // the non-breaking space between R and the amount, and between thousands

test("whole rand: thousands grouped with non-breaking spaces, no cents", () => {
  assert.equal(formatMoney(31840000), `R${S}318${S}400`);
  assert.equal(formatMoney(100), `R${S}1`);
  assert.equal(formatMoney(0), `R${S}0`);
  assert.equal(formatMoney(123456789), `R${S}1${S}234${S}567.89`);
});

test("cents only when the amount has them, unless asked", () => {
  assert.equal(formatMoney(125050), `R${S}1${S}250.50`);
  assert.equal(formatMoney(125000, { cents: "always" }), `R${S}1${S}250.00`);
  assert.equal(formatMoney(125050, { cents: "never" }), `R${S}1${S}251`);
  assert.equal(formatMoney(125049, { cents: "never" }), `R${S}1${S}250`);
});

test("negative amounts use a real minus sign", () => {
  assert.equal(formatMoney(-120000), `−R${S}1${S}200`);
});

test("missing or broken amounts show a dash, never R 0", () => {
  for (const v of [null, undefined, "", "abc", NaN]) assert.equal(formatMoney(v), "—");
  assert.equal(formatMoney(null, { empty: "" }), "");
});

test("string cents from the API are accepted", () => {
  assert.equal(formatMoney("250000"), `R${S}2${S}500`);
});
