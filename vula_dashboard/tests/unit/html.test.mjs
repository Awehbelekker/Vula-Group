// escapeHtml (lib/html.js): customer text goes into the print windows as text, never markup.
import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeHtml } from "../../src/lib/html.js";

test("markup and attribute quotes are escaped", () => {
  assert.equal(escapeHtml('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(escapeHtml("<script>x</script>"), "&lt;script&gt;x&lt;/script&gt;");
  assert.equal(escapeHtml("O'Brien & Sons"), "O&#39;Brien &amp; Sons");
});

test("plain text, numbers and empty values pass through", () => {
  assert.equal(escapeHtml("14 Beach Rd, Sea Point"), "14 Beach Rd, Sea Point");
  assert.equal(escapeHtml(3), "3");
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
});
