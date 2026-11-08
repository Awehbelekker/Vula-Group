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
 */
import { supabase } from './supabase'

export async function stillSignedIn() {
  try {
    const { data, error } = await supabase.auth.getUser()
    if (!error) return Boolean(data?.user)
    if (error.name === 'AuthSessionMissingError') return false
    if (error.status === 401 || error.status === 403) return false
    return null
  } catch {
    return null
  }
}
