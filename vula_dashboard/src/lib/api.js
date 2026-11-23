/**
 * api.js — one way to call Vula's API that never treats a refusal as success (23 Nov 2026 audit:
 * 162 saves didn't check the server's answer, 33 more judged it only by an `error` key while
 * FastAPI says `detail`, and 175 loads showed "No … yet" when the load had failed).
 *
 *   const d = await api(`/v1/commerce/${t}/admin/expenses`, { method: 'POST', body: {...} })
 *     → the JSON on 2xx; throws ApiError(message, status, data) on anything else, on a network
 *       failure, or on a 2xx whose body carries `error` (older routes answer 200 {error}).
 *   const { ok, data, error } = await tryApi(path, opts)   // never throws
 *
 * The token is added by the fetch patch in authFetch.js (installAuthFetch).
 */
import { VULA_API } from './authFetch'

export class ApiError extends Error {
  constructor(message, status = 0, data = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

/** The server's own words for what went wrong, in plain text. */
export function errorText(d, fallback = "That didn't go through. Try again.") {
  if (!d) return fallback
  const v = d.detail ?? d.error ?? d.message
  if (typeof v === 'string' && v.trim()) return v
  if (Array.isArray(v) && v.length) {
    // FastAPI validation errors: [{loc, msg}]
    const m = v.map((x) => (x && typeof x === 'object' ? x.msg : String(x))).filter(Boolean)
    if (m.length) return m.join('; ')
  }
  return fallback
}

export async function api(path, { method = 'GET', body, headers, fallback } = {}) {
  const url = /^https?:\/\//.test(path) ? path : `${VULA_API}${path}`
  const opts = { method, headers: { ...(headers || {}) } }
  if (body !== undefined) {
    if (body instanceof FormData) opts.body = body
    else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json' }
  }
  let r
  try {
    r = await fetch(url, opts)
  } catch {
    throw new ApiError("Couldn't reach Vula. Check your connection and try again.", 0)
  }
  const d = await r.json().catch(() => null)
  if (!r.ok) {
    const msg = r.status === 403 && !(d && (d.detail || d.error))
      ? "Your login doesn't include this." : errorText(d, fallback)
    throw new ApiError(msg, r.status, d)
  }
  if (d && typeof d === 'object' && !Array.isArray(d) && d.error && d.ok !== true) {
    throw new ApiError(errorText(d, fallback), r.status, d)
  }
  return d ?? {}
}

export async function tryApi(path, opts) {
  try {
    return { ok: true, data: await api(path, opts), error: null, status: 200 }
  } catch (e) {
    return { ok: false, data: e.data ?? null, error: e.message, status: e.status ?? 0 }
  }
}
