/**
 * deviceData.js — what Vula keeps on this phone for the signed-in person, and clearing it when
 * they sign out (2026-10-27, PWA pass 1). A shop phone is often shared: the next person to sign
 * in must not find the last person's shared files, open business or half-done stock count.
 *
 * Cleared on sign-out:
 *   - Cache Storage "vula-share" — photos / PDFs shared into Vula from the phone (public/sw.js)
 *   - vula.masterTenant (the business master last opened), vula.go (a link waiting for login),
 *     vula.ui.tenant.* (each business's cached design choice)
 *   - vula.stockq.* / vula.stockt.* — a stock count's scans not yet sent. If there are any, the
 *     person is asked first: signing out loses them.
 * Kept: this device's own settings (light/dark, receipt sound and volume, ?ui= preview), and
 * the app's own files in the service worker's shell cache (no one's data is ever in it).
 */
import { confirmDialog } from '../components/ui/index.jsx'

const PERSON_KEYS = ['vula.masterTenant', 'vula.go']
const PERSON_PREFIXES = ['vula.ui.tenant.', 'vula.stockq.', 'vula.stockt.']
const PERSON_CACHES = ['vula-share']

function keys(storage) {
  try {
    return Array.from({ length: storage.length }, (_, i) => storage.key(i)).filter(Boolean)
  } catch { return [] }
}

/** Scans waiting to be sent across every stock count kept on this phone. */
export function unsentStockScans() {
  let n = 0
  for (const k of keys(window.localStorage)) {
    if (!k.startsWith('vula.stockq.')) continue
    try { n += (JSON.parse(localStorage.getItem(k)) || []).length } catch { /* unreadable: ignore */ }
  }
  return n
}

/** Removes what belongs to the signed-in person. Never throws. */
export async function clearPersonData() {
  for (const store of [window.localStorage, window.sessionStorage]) {
    for (const k of keys(store)) {
      if (PERSON_KEYS.includes(k) || PERSON_PREFIXES.some(p => k.startsWith(p))) {
        try { store.removeItem(k) } catch { /* storage off */ }
      }
    }
  }
  try {
    if (window.caches) await Promise.all(PERSON_CACHES.map(c => caches.delete(c)))
  } catch { /* no Cache Storage here */ }
}

/** Before signing out: false when the person chose to stay signed in to keep unsent scans. */
export async function readyToSignOut() {
  const n = unsentStockScans()
  if (!n) return true
  return confirmDialog(
    `${n} stock scan${n === 1 ? ' hasn’t' : 's haven’t'} been sent yet. They’re only on this phone, and signing out deletes them. Sign out anyway?`,
    { danger: true, confirmLabel: 'Sign out anyway', cancelLabel: 'Stay signed in' })
}
