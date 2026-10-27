/**
 * scanQueue.js — Smart Scanner photos taken with no signal (2026-10-27, PWA pass 4). They wait on
 * this phone, in Cache Storage ("vula-scanq": a camera photo is far too big for localStorage), and
 * the scanner reads them when the phone is back online. Reading books nothing: each one is still
 * checked and saved by the person, as usual. Signing out deletes them, after asking
 * (lib/deviceData.js).
 */
export const SCAN_QUEUE = 'vula-scanq'
const PREFIX = '/scan-queue/'
const CHANGED = 'vula-scanq-change'

const available = () => typeof window !== 'undefined' && !!window.caches

/** Keeps a photo on this phone: its id, or null when this browser can't (private mode, storage full). */
export async function queueScan(file, docType = 'auto') {
  if (!available() || !file) return null
  try {
    const cache = await caches.open(SCAN_QUEUE)
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    await cache.put(`${PREFIX}${id}`, new Response(file, { headers: {
      'Content-Type': file.type || 'image/jpeg', 'X-Doc-Type': docType,
      'X-Name': encodeURIComponent(file.name || 'photo'), 'X-Taken': new Date().toISOString() } }))
    window.dispatchEvent(new Event(CHANGED))
    return id
  } catch { return null }
}

/** The photos waiting, oldest first: [{id, docType, taken}]. */
export async function waitingScans() {
  if (!available()) return []
  try {
    const cache = await caches.open(SCAN_QUEUE)
    const keys = await cache.keys()
    const out = []
    for (const req of keys) {
      const res = await cache.match(req)
      out.push({ id: new URL(req.url).pathname.slice(PREFIX.length), docType: res?.headers.get('X-Doc-Type') || 'auto',
        taken: res?.headers.get('X-Taken') || '' })
    }
    return out.sort((a, b) => a.id.localeCompare(b.id))
  } catch { return [] }
}

/** One waiting photo as a File, or null. */
export async function waitingScanFile(id) {
  try {
    const res = await (await caches.open(SCAN_QUEUE)).match(`${PREFIX}${id}`)
    if (!res) return null
    const blob = await res.blob()
    return new File([blob], decodeURIComponent(res.headers.get('X-Name') || 'photo'), { type: blob.type || 'image/jpeg' })
  } catch { return null }
}

export async function dropScan(id) {
  try {
    await (await caches.open(SCAN_QUEUE)).delete(`${PREFIX}${id}`)
    window.dispatchEvent(new Event(CHANGED))
  } catch { /* nothing kept */ }
}

/** Calls back whenever a photo is kept or dropped (this tab). Returns the unsubscribe. */
export function onScanQueueChange(fn) {
  window.addEventListener(CHANGED, fn)
  return () => window.removeEventListener(CHANGED, fn)
}

/** A fetch that never reached the server (no signal), as opposed to the server saying no. */
export const isOffline = (err) => (typeof navigator !== 'undefined' && navigator.onLine === false) || err instanceof TypeError
