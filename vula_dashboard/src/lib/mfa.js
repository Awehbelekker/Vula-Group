/**
 * mfa.js — two-step sign-in with an authenticator app (18 Nov 2026, pre-launch audit: none of
 * the 15 logins had it). Supabase Auth's own TOTP: a login that has set one up must give the
 * 6-digit code after its password, Google or WhatsApp sign-in, and Vula's server refuses a
 * session that didn't (vula_mind/vula/api/master_auth.py two_step_missing). Roles the server
 * lists (GET /v1/auth/two-step-policy) must set one up before they get in.
 */
import { supabase } from './supabase'
import { VULA_API } from './authFetch'

let policy = null

/** The roles that must set up two-step sign-in (lower-case). Never throws; [] when unknown. */
export async function requiredRoles() {
  if (policy) return policy
  try {
    const r = await fetch(`${VULA_API}/v1/auth/two-step-policy`)
    const d = r.ok ? await r.json() : {}
    policy = (d.required_roles || []).map((x) => String(x).toLowerCase())
  } catch {
    return []
  }
  return policy
}

/** The verified authenticator app on this login, or null. */
export async function verifiedFactor() {
  const { data, error } = await supabase.auth.mfa.listFactors()
  if (error) throw error
  return (data?.totp || []).find((f) => f.status === 'verified') || null
}

/**
 * What this session still needs before the dashboard opens:
 *   'code'  — the login has an app and this session hasn't given its code yet;
 *   'setup' — the role must have two-step and the login hasn't set it up;
 *   null    — nothing.
 */
export async function twoStepNeeded(role) {
  const { data, error } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel()
  if (error) throw error
  if (data?.nextLevel === 'aal2' && data?.currentLevel !== 'aal2') return 'code'
  if (data?.currentLevel === 'aal2') return null
  const need = await requiredRoles()
  if (role && need.includes(String(role).toLowerCase()) && !(await verifiedFactor())) return 'setup'
  return null
}

/** Give the app's code for this session. */
export async function giveCode(code) {
  const factor = await verifiedFactor()
  if (!factor) throw new Error("This login has no authenticator app set up.")
  const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId: factor.id, code: cleanCode(code) })
  if (error) throw new Error(codeError(error))
}

/** Start setting up an app: { factorId, qr (an image URL), secret }. Leftover unfinished set-ups go first. */
export async function startSetup() {
  const { data: all } = await supabase.auth.mfa.listFactors()
  for (const f of all?.all || []) {
    if (f.factor_type === 'totp' && f.status !== 'verified') await supabase.auth.mfa.unenroll({ factorId: f.id })
  }
  const { data, error } = await supabase.auth.mfa.enroll({ factorType: 'totp', friendlyName: `Vula ${new Date().toISOString().slice(0, 10)}` })
  if (error) throw error
  return { factorId: data.id, qr: data.totp?.qr_code, secret: data.totp?.secret }
}

/** Finish setting up: the first code from the app proves it's scanned. */
export async function finishSetup(factorId, code) {
  const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId, code: cleanCode(code) })
  if (error) throw new Error(codeError(error))
}

/** Turn it off for this login (needs a session that gave the code). */
export async function turnOff() {
  const factor = await verifiedFactor()
  if (!factor) return
  const { error } = await supabase.auth.mfa.unenroll({ factorId: factor.id })
  if (error) throw error
}

export const cleanCode = (code) => String(code || '').replace(/\D/g, '').slice(0, 6)

export function codeError(error) {
  const msg = String(error?.message || '')
  if (/invalid|expired|code/i.test(msg)) return "That code didn't work. Check the time on your phone, then type the newest code."
  return msg || "That didn't go through. Try again."
}
