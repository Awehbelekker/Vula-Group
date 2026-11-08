/**
 * session.js — notice when the browser's sign-in has lapsed while the app still shows someone as
 * signed in (8 Nov 2026).
 *
 * The auth store (store/auth.js) keeps who is signed in in localStorage, but the Supabase session
 * behind it can end on its own: the refresh token expires, the phone was off for a long time, or
 * the session was signed out on another tab. The app then still showed the Master screen while
 * every API call went out with no token and came back 401, so lists were empty (the Master tenant
 * switcher showed only one business). Now any of these sends the person back to sign-in:
 *   - on start, there is no Supabase session at all;
 *   - Supabase reports SIGNED_OUT;
 *   - a Vula API call made with no token comes back 401 (lib/authFetch.js fires LAPSED_EVENT).
 */
export const LAPSED_EVENT = 'vula-session-lapsed'

/** True when an API answer means "you are not signed in": a 401 to a request that had no token. */
export function isLapsed(status, hadToken) {
  return status === 401 && !hadToken
}

/** Tell the app the sign-in has lapsed (safe to call anywhere, even without a window). */
export function reportLapsed() {
  try { window.dispatchEvent(new Event(LAPSED_EVENT)) } catch { /* not in a browser */ }
}

/**
 * Watch the session while someone is shown as signed in; onLapsed runs at most once.
 * `auth` is supabase.auth (passed in so this can be tested without a network).
 * Returns a function that stops watching.
 */
export function watchSession(auth, onLapsed) {
  let done = false
  const lapse = () => { if (!done) { done = true; onLapsed() } }
  Promise.resolve()
    .then(() => auth.getSession())
    .then(({ data } = {}) => { if (!data?.session) lapse() })
    .catch(() => { /* can't tell (offline): leave the person signed in */ })
  let sub = null
  try {
    sub = auth.onAuthStateChange((event) => { if (event === 'SIGNED_OUT') lapse() })
  } catch { sub = null }
  let target = null
  try { target = window; target.addEventListener(LAPSED_EVENT, lapse) } catch { target = null }
  return () => {
    done = true
    try { sub?.data?.subscription?.unsubscribe?.() } catch { /* already gone */ }
    try { target?.removeEventListener(LAPSED_EVENT, lapse) } catch { /* not in a browser */ }
  }
}
