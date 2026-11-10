// sessionVerdict (lib/sessionVerdict.js): is this phone's sign-in still good?
// Run: npm run test:unit
import { test } from "node:test";
import assert from "node:assert/strict";
import { signedInFromAuthResult as verdict } from "../../src/lib/sessionVerdict.js";

const err = (name, status, code) => ({ data: { user: null }, error: Object.assign(new Error(code || name), { name, status, code }) });

test("a user came back: signed in", () => {
  assert.equal(verdict({ data: { user: { id: "u1" } }, error: null }), true);
});

test("an expired or revoked refresh token is signed out (DIGG's phone, 10 Nov 2026)", () => {
  assert.equal(verdict(err("AuthApiError", 400, "refresh_token_not_found")), false);
  assert.equal(verdict(err("AuthApiError", 400, "refresh_token_already_used")), false);
});

test("no session, or a token the server refuses, is signed out", () => {
  assert.equal(verdict(err("AuthSessionMissingError", undefined)), false);
  assert.equal(verdict(err("AuthApiError", 401, "bad_jwt")), false);
  assert.equal(verdict(err("AuthApiError", 403, "session_not_found")), false);
  assert.equal(verdict({ data: { user: null }, error: null }), false);
});

test("offline or the auth server failing never signs anyone out", () => {
  assert.equal(verdict(err("AuthRetryableFetchError", 0)), null);
  assert.equal(verdict(err("AuthRetryableFetchError", undefined)), null);
  assert.equal(verdict(err("AuthApiError", 500, "unexpected_failure")), null);
  assert.equal(verdict(err("AuthApiError", 503)), null);
});
