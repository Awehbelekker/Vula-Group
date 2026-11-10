// mainScreen (lib/mainScreen.js): the phone bar's button for a business's everyday screen.
// Run: npm run test:unit
import { test } from "node:test";
import assert from "node:assert/strict";
import { mainScreen } from "../../src/lib/mainScreen.js";

const page = (id, section = "money") => ({ id, label: id[0].toUpperCase() + id.slice(1), section });

test("a shop gets Orders", () => {
  assert.equal(mainScreen([page("overview", "overview"), page("orders", "sell"), page("invoices")]).id, "orders");
});

test("a business that doesn't sell (DIGG) gets Invoices", () => {
  assert.equal(mainScreen([page("overview", "overview"), page("projects", "work"), page("invoices")]).id, "invoices");
});

test("nothing is offered that the login can't already see", () => {
  assert.equal(mainScreen([page("overview", "overview"), page("contacts", "crm")]), null);
  assert.equal(mainScreen([]), null);
  assert.equal(mainScreen(undefined), null);
});
