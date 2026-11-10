/**
 * sessionVerdict.js — what Supabase's answer says about this phone's sign-in (10 Nov 2026).
 *
 * lib/session.js asks supabase.auth.getUser(); this reads the result:
 *   true  — a user came back: the sign-in is good.
 *   false — the auth server answered and there is no valid sign-in: no session, or any 4xx
 *           (an expired or revoked refresh token is a 400 "Invalid Refresh Token", a bad or
 *           expired token 401/403). The person goes back to the login page.
 *   null  — no answer (offline, a timeout) or the auth server failed (5xx): couldn't tell, so
 *           nobody is signed out for that.
 *
 * 10 Nov 2026: DIGG's owner's phone renewed its sign-in at 04:55, Supabase refused the expired
 * refresh token with a 400, and every request after that was a 401. Only 401/403 counted as
 * "signed out", so the 400 read as "couldn't tell": the app never went back to the login page,
 * and Invoices sat loading for ever.
 */
export function signedInFromAuthResult(result) {
  const error = result?.error;
  if (!error) return result?.data?.user ? true : false;
  if (error.name === "AuthSessionMissingError") return false;
  const status = Number(error.status);
  if (status >= 400 && status < 500) return false;
  return null;
}
