/**
 * session.js — has this phone's sign-in run out? (8 Nov 2026)
 *
 * The dashboard remembers who is signed in (store/auth.js) separately from the Supabase session
 * that proves it. When the session runs out (refresh token expired or revoked, signed out on
 * another device), the dashboard used to keep showing the last screens while Vula turned every
 * request away. On a 401 from Vula, App.jsx asks Supabase itself:
 *   false — Supabase says there is no valid session: the sign-in has run out.
 *   true  — the session is fine (the 401 was something else): carry on.
 *   null  — couldn't tell (offline, Supabase down): carry on; never sign anyone out for that.
 * Which answer is which: lib/sessionVerdict.js (a refused refresh token, a 400, is "run out").
 */
import { supabase } from './supabase'
import { signedInFromAuthResult } from './sessionVerdict'
import { twoStepNeeded } from './mfa'

/** `role` is the signed-in role: a session that still owes its two-step code, or a role that must
 * set two-step up and hasn't, counts as not signed in (18 Nov 2026) — Vula's server refuses it, so
 * the login page asks for the code instead of every screen failing to load. */
export async function stillSignedIn(role) {
  try {
    const verdict = signedInFromAuthResult(await supabase.auth.getUser())
    if (verdict !== true) return verdict
    try {
      if (await twoStepNeeded(role)) return false
    } catch { /* couldn't tell: keep them signed in */ }
    return true
  } catch {
    return null
  }
}
