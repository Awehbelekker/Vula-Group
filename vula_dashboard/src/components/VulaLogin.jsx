/**
 * VulaLogin.jsx
 *
 * Handles these auth flows:
 *   0. Log in with WhatsApp — QR / button / 6-digit code (WhatsAppLogin.jsx, 15 Oct 2026)
 *   1. Normal sign-in (email + password)
 *   2. Password recovery — Supabase puts #access_token in URL after clicking
 *      the reset email; we detect this and show a "set new password" form
 *   3. First-time invite — same token flow as recovery
 *
 * Master user (Richard/Ian): awehbelekker@gmail.com
 * role = 'master'  → sees all tenants
 * role = 'owner'   → client, sees only their tenant
 */

import LoginV2 from './login/LoginV2'

/** The redesign gets its own screen (login/LoginV2.jsx); the sign-in logic is shared (useLogin). */
export default function VulaLogin({ ...props }) {
  return <LoginV2 {...props} />
}

