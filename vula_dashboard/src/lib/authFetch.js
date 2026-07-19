/**
 * authFetch — fetch with the signed-in user's Supabase session JWT attached.
 * Required for /v1/master/* and the master-gated tenant endpoints: the backend verifies the
 * token server-side (vula/api/master_auth.py) instead of trusting the client's role label.
 */
import { supabase } from './supabase'

export const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

export async function authFetch(path, opts = {}) {
  const { data } = await supabase.auth.getSession()
  const token = data?.session?.access_token
  const headers = { ...(opts.headers || {}) }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(`${VULA_API}${path}`, { ...opts, headers })
  if (resp.status === 401) throw new Error('Session expired — please sign in again.')
  if (resp.status === 403) throw new Error('Master access required.')
  return resp.json()
}

// Paths that the backend's tenant_admin_guard protects (ENFORCE_TENANT_AUTH). One global
// wrapper instead of touching ~40 components: every fetch to a guarded Vula path gets the
// signed-in user's token attached transparently.
const GUARDED = /\/v1\/(commerce\/[^/]+\/admin(\/|$)|team\/|users\/|master(\/|$)|admin\/)|\/scrape\//

/** Patch window.fetch once (call from main.jsx) so ALL existing components send the JWT. */
export function installAuthFetch() {
  if (window.__vulaAuthFetchInstalled) return
  window.__vulaAuthFetchInstalled = true
  const raw = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    try {
      const url = typeof input === 'string' ? input : (input?.url || '')
      if (url.startsWith(VULA_API) && GUARDED.test(url)) {
        const { data } = await supabase.auth.getSession()
        const token = data?.session?.access_token
        if (token) {
          const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined) || {})
          if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
          init = { ...init, headers }
        }
      }
    } catch { /* never let auth decoration break a request */ }
    return raw(input, init)
  }
}
