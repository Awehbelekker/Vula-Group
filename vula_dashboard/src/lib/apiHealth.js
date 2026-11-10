/**
 * apiHealth.js — is Vula's server answering? (9 Nov 2026 audit)
 *
 * With the phone online but the server down, lists used to render empty, which a business reads
 * as lost orders and invoices. lib/authFetch.js feeds every Vula API outcome in here;
 * OfflineBanner listens for API_HEALTH_EVENT and says Vula can't be reached.
 */
export const API_HEALTH_EVENT = 'vula-api-health'

/** Whether one outcome means the server is down: no answer while online, or 502/503/504. */
export function isDownOutcome({ status = null, networkError = false, online = true }) {
  if (networkError) return online !== false
  return [502, 503, 504].includes(status)
}

let down = false

/** Record an outcome; tells the app only when the state changes. Returns the current state. */
export function reportApiOutcome(outcome, target = typeof window !== 'undefined' ? window : null) {
  const now = isDownOutcome(outcome)
  if (now !== down) {
    down = now
    try { target?.dispatchEvent(new CustomEvent(API_HEALTH_EVENT, { detail: { down } })) } catch { /* no window */ }
  }
  return down
}
