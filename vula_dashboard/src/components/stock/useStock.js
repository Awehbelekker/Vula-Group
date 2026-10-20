/**
 * useStock.js — everything behind the Stock tab, shared by VulaStock (current look) and
 * stock/StockV2 (the redesign, 2026-10-20). Moved verbatim out of VulaStock.jsx: the camera
 * barcode reader, barcode → product resolution (and linking an unknown code), the stock-take
 * list and count session (scans queued in this browser and sent in batches, each with its own
 * id so a re-sent batch never double-counts — migration 183), receiving a delivery, and quick
 * adjust with the item's movement history (migration 182).
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { useAuthStore } from '../../store/auth'
import { toast, confirmDialog } from '../ui/index.jsx'

// Exact amounts (migration 210): 2.5 kg stays 2.5 — never rounded to a whole number.
export const toQty = v => { const n = parseFloat(String(v).replace(',', '.')); return isNaN(n) ? NaN : Math.round(n * 10000) / 10000 }

const H = { 'Content-Type': 'application/json' }   // JWT attached by lib/authFetch

export async function api(tenantId, path, opts = {}) {
  const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin${path}`, { headers: H, ...opts })
  let d = {}
  try { d = await r.json() } catch { /* empty body */ }
  if (!r.ok) { const e = new Error(d.detail || `Request failed (${r.status})`); e.status = r.status; throw e }
  return d
}

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`)
export const itemKey = (pid, vid) => `${pid}:${vid || ''}`
const loadJSON = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch { return d } }
const saveJSON = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* storage off */ } }

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const o = ctx.createOscillator(); const g = ctx.createGain()
    o.frequency.value = 1100; g.gain.value = 0.08
    o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.09)
    o.onended = () => ctx.close()
  } catch { /* no audio */ }
  try { navigator.vibrate?.(60) } catch { /* no vibration */ }
}

export const itemName = (hit) => {
  const base = hit?.product?.name || 'Unknown product'
  const opts = hit?.variant?.option_values
  return opts && typeof opts === 'object' ? `${base} — ${Object.values(opts).join(' / ')}` : base
}

export const REASON = { count: 'Stock-take', receive: 'Received', adjust: 'Adjusted', sale: 'Sold', refund: 'Refunded', cancel: 'Cancelled', import: 'Imported' }

// ── Camera barcode scanner ────────────────────────────────────────────────────

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code']

/** The browser's BarcodeDetector (Android Chrome) or, where it's missing (iPhone Safari),
 * @zxing/browser loaded on first use. Attach videoRef to a <video muted playsInline>. */
export function useBarcodeCamera({ onCode, paused }) {
  const videoRef = useRef(null)
  const last = useRef({ code: '', at: 0 })
  const pausedRef = useRef(paused)
  const [on, setOn] = useState(false)
  const [err, setErr] = useState('')
  const [manual, setManual] = useState('')
  pausedRef.current = paused

  const emit = useCallback((code) => {
    const now = Date.now()
    // One item held in front of the camera is read many times a second — one count per 1.5s.
    if (pausedRef.current || (code === last.current.code && now - last.current.at < 1500)) return
    last.current = { code, at: now }
    beep()
    onCode(code)
  }, [onCode])

  useEffect(() => {
    if (!on) return
    let stream, timer, zxingControls, stopped = false
    ;(async () => {
      setErr('')
      try {
        if ('BarcodeDetector' in window) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
          if (stopped) return
          videoRef.current.srcObject = stream
          await videoRef.current.play()
          const supported = await window.BarcodeDetector.getSupportedFormats?.() || FORMATS
          const det = new window.BarcodeDetector({ formats: FORMATS.filter(f => supported.includes(f)) })
          const tick = async () => {
            if (stopped) return
            try {
              const found = await det.detect(videoRef.current)
              if (found[0]?.rawValue) emit(found[0].rawValue)
            } catch { /* frame not ready */ }
            timer = setTimeout(tick, 200)
          }
          tick()
        } else {
          const { BrowserMultiFormatReader } = await import('@zxing/browser')
          if (stopped) return
          const reader = new BrowserMultiFormatReader()
          zxingControls = await reader.decodeFromConstraints(
            { video: { facingMode: 'environment' } }, videoRef.current,
            (result) => { if (result) emit(result.getText()) })
          if (stopped) zxingControls.stop()
        }
      } catch (e) {
        setErr(e?.name === 'NotAllowedError'
          ? 'Camera permission was refused — allow it in the browser, or type the barcode below.'
          : 'Could not start the camera on this device — type the barcode below.')
        setOn(false)
      }
    })()
    return () => {
      stopped = true
      clearTimeout(timer)
      zxingControls?.stop()
      stream?.getTracks().forEach(t => t.stop())
    }
  }, [on, emit])

  const submitManual = (e) => { e.preventDefault(); if (manual.trim()) { onCode(manual.trim()); setManual('') } }
  return { videoRef, on, setOn, err, manual, setManual, submitManual }
}

// ── Product search + barcode resolution ───────────────────────────────────────

export function useProductSearch(products) {
  const [q, setQ] = useState('')
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return []
    return products.filter(p => !p.archived && `${p.name} ${p.sku || ''} ${p.barcode || ''}`.toLowerCase().includes(s)).slice(0, 8)
  }, [q, products])
  return { q, setQ, hits }
}

/** Turns a scanned code into {product, variant}; asks to link a code nothing carries
 * (`unknown` is set until linkTo(product) or skip()). */
export function useResolver(tenantId, products) {
  const [unknown, setUnknown] = useState(null)   // { code, resolve }
  const cache = useRef({})
  const resolve = useCallback(async (code) => {
    if (cache.current[code]) return cache.current[code]
    const local = products.find(p => p.barcode === code || p.sku === code)
    if (local) return (cache.current[code] = { product: local, variant: null })
    try {
      const hit = await api(tenantId, `/products/lookup?barcode=${encodeURIComponent(code)}`)
      return (cache.current[code] = hit)
    } catch (e) {
      if (e.status !== 404) throw e
      return new Promise((res) => setUnknown({ code, resolve: res }))
    }
  }, [tenantId, products])

  const linkTo = async (p) => {
    try {
      const hit = await api(tenantId, `/products/${p.id}/barcode`, { method: 'POST', body: JSON.stringify({ barcode: unknown.code }) })
      cache.current[unknown.code] = hit
      unknown.resolve(hit)
    } catch (e) { toast(e.message); unknown.resolve(null) }
    setUnknown(null)
  }
  const skip = () => { unknown.resolve(null); setUnknown(null) }
  return { resolve, unknown, linkTo, skip, waiting: !!unknown }
}

// ── Stock-take ────────────────────────────────────────────────────────────────

export function useStockTakes(tenantId) {
  const full = useAuthStore(s => s.full)
  const [counts, setCounts] = useState([])
  const [countId, setCountId] = useState(null)
  const [note, setNote] = useState('')
  const [review, setReview] = useState(null)
  const [msg, setMsg] = useState('')
  const load = useCallback(() => api(tenantId, '/stock/counts').then(d => setCounts(d.counts || [])).catch(e => setMsg(e.message)), [tenantId])
  useEffect(() => { load() }, [load])

  const start = async () => {
    try { const c = await api(tenantId, '/stock/counts', { method: 'POST', body: JSON.stringify({ note }) }); setNote(''); setCountId(c.id) }
    catch (e) { setMsg(e.message) }
  }
  const close = () => { setCountId(null); setReview(null); load() }
  const open = counts.filter(c => c.status === 'open')
  return { full, counts, open, countId, setCountId, note, setNote, review, setReview, msg, start, close }
}

export function useCountSession({ tenantId, products, countId, setReview, onClose }) {
  const qKey = `vula.stockq.${tenantId}.${countId}`
  const tKey = `vula.stockt.${tenantId}.${countId}`
  const [queue, setQueue] = useState(() => loadJSON(qKey, []))
  const [tally, setTally] = useState(() => loadJSON(tKey, {}))   // itemKey → {name, counted}
  const [status, setStatus] = useState('open')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const syncing = useRef(false)
  const resolver = useResolver(tenantId, products)
  const { resolve } = resolver

  useEffect(() => { saveJSON(qKey, queue) }, [qKey, queue])
  useEffect(() => { saveJSON(tKey, tally) }, [tKey, tally])

  const flush = useCallback(async () => {
    if (syncing.current || !navigator.onLine) return
    const batch = loadJSON(qKey, [])
    if (!batch.length) return
    syncing.current = true
    try {
      const d = await api(tenantId, `/stock/counts/${countId}/scans`, { method: 'POST', body: JSON.stringify({ scans: batch }) })
      const sent = new Set(batch.map(s => s.scan_id))
      setQueue(q => q.filter(s => !sent.has(s.scan_id)))
      setTally(t => {
        const next = { ...t }
        d.results.forEach((r, i) => {
          const s = batch[i]; const k = itemKey(s.product_id, s.variant_id)
          if (r.counted != null && next[k]) next[k] = { ...next[k], counted: r.counted }
        })
        return next
      })
      if (d.results.some(r => r.counted == null)) setMsg('Some scans were not accepted — this stock-take may have been applied or cancelled.')
    } catch (e) {
      if (e.status && e.status < 500) setMsg(e.message)   // network trouble: keep the queue and retry
    } finally { syncing.current = false }
  }, [tenantId, countId, qKey])

  useEffect(() => {
    const t = setInterval(flush, 4000)
    window.addEventListener('online', flush)
    return () => { clearInterval(t); window.removeEventListener('online', flush) }
  }, [flush])

  const record = useCallback((hit, { add, set } = {}) => {
    if (!hit?.product) return
    const pid = hit.product.id; const vid = hit.variant?.id || null; const k = itemKey(pid, vid)
    setQueue(q => [...q, { scan_id: newId(), product_id: pid, variant_id: vid, ...(set != null ? { set } : { add: add ?? 1 }) }])
    setTally(t => {
      const cur = t[k]?.counted || 0
      return { ...t, [k]: { name: itemName(hit), counted: set != null ? set : cur + (add ?? 1), pid, vid } }
    })
    setTimeout(flush, 300)
  }, [flush])

  const onCode = useCallback(async (code) => {
    try { const hit = await resolve(code); if (hit) record(hit) } catch (e) { setMsg(e.message) }
  }, [resolve, record])

  async function openReview() {
    setBusy(true); setMsg('')
    await flush()
    try { const r = await api(tenantId, `/stock/counts/${countId}`); setReview(r); setStatus(r.count.status) }
    catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  // A finished (applied/cancelled) count opens straight on its review; an open one on the scanner.
  useEffect(() => {
    api(tenantId, `/stock/counts/${countId}`).then(r => {
      setStatus(r.count.status)
      if (r.count.status !== 'open') setReview(r)
    }).catch(e => setMsg(e.message))
  }, [tenantId, countId, setReview])

  async function applyCount() {
    if (!(await confirmDialog('Set stock to these counts? Items you did not count are left as they are.'))) return
    setBusy(true)
    try {
      const r = await api(tenantId, `/stock/counts/${countId}/apply`, { method: 'POST' })
      saveJSON(qKey, []); saveJSON(tKey, {})
      await openReview()
      // after the reload: openReview clears the message, which used to wipe this one (2026-10-20)
      setMsg(`Applied ${r.applied} item${r.applied === 1 ? '' : 's'}${r.failed.length ? ` — ${r.failed.length} could not be updated` : ''}.`)
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }

  async function cancelCount() {
    if (!(await confirmDialog('Cancel this stock-take? Nothing will change.', { danger: true, confirmLabel: 'Yes' }))) return
    try { await api(tenantId, `/stock/counts/${countId}/cancel`, { method: 'POST' }); onClose() } catch (e) { setMsg(e.message) }
  }

  /** A typed count for one line replaces what was scanned for it. */
  const setCount = (l, value) => {
    const v = toQty(value)
    if (!isNaN(v) && v >= 0) record({ product: { id: l.pid, name: l.name }, variant: l.vid ? { id: l.vid } : null }, { set: v })
  }

  const lines = Object.entries(tally).sort((a, b) => a[1].name.localeCompare(b[1].name))
  return { queue, status, msg, busy, resolver, record, onCode, openReview, applyCount, cancelCount, setCount, lines }
}

// ── Receive a delivery ────────────────────────────────────────────────────────

export function useReceive(tenantId, products) {
  const [pos, setPos] = useState([])
  const [po, setPo] = useState(null)
  const [lines, setLines] = useState({})   // itemKey → {pid, vid, name, qty, ordered, cost}
  const [ref, setRef] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const resolver = useResolver(tenantId, products)
  const { resolve } = resolver
  useEffect(() => {
    api(tenantId, '/purchase-orders').then(d => setPos((d.purchase_orders || []).filter(p => ['sent', 'draft'].includes(p.status)))).catch(() => {})
  }, [tenantId])

  function choosePo(id) {
    const p = pos.find(x => x.id === id) || null
    setPo(p)
    const next = {}
    for (const it of (p?.items || [])) {
      if (!it.product_id) continue
      const prod = products.find(x => x.id === it.product_id)
      next[itemKey(it.product_id, it.variant_id)] = { pid: it.product_id, vid: it.variant_id || null,
        name: prod?.name || it.name || 'Item', qty: 0, ordered: Number(it.quantity) || 0, cost: it.unit_cost_cents ?? null }
    }
    setLines(next)
  }
  const add = useCallback((hit, n = 1) => {
    if (!hit?.product) return
    const k = itemKey(hit.product.id, hit.variant?.id)
    setLines(ls => ({ ...ls, [k]: { pid: hit.product.id, vid: hit.variant?.id || null, name: itemName(hit), ordered: null, cost: null,
      ...ls[k], qty: (ls[k]?.qty || 0) + n } }))
  }, [])
  const onCode = useCallback(async (code) => {
    try { const hit = await resolve(code); if (hit) add(hit) } catch (e) { setMsg(e.message) }
  }, [resolve, add])

  async function book() {
    const body = { po_id: po?.id, reference: ref || po?.po_number || null,
      lines: Object.values(lines).filter(l => l.qty > 0).map(l => ({ product_id: l.pid, variant_id: l.vid, quantity: l.qty,
        ...(l.cost != null ? { unit_cost_cents: l.cost } : {}) })) }
    if (!body.lines.length) { setMsg('Nothing received yet — scan or type the quantities.'); return }
    setBusy(true)
    try {
      const r = await api(tenantId, '/stock/receive', { method: 'POST', body: JSON.stringify(body) })
      setMsg(`Booked ${r.received} line${r.received === 1 ? '' : 's'} into stock${r.failed?.length ? ` — ${r.failed.length} failed` : ''}.`)
      setLines({}); setPo(null); setRef('')
      if (po) setPos(ps => ps.filter(p => p.id !== po.id))
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }

  const allAsOrdered = () => setLines(ls => Object.fromEntries(Object.entries(ls).map(([k, l]) => [k, { ...l, qty: l.ordered ?? l.qty }])))
  const setQty = (k, value) => { const v = Math.max(0, toQty(value) || 0); setLines(ls => ({ ...ls, [k]: { ...ls[k], qty: v } })) }
  const rows = Object.entries(lines)
  return { pos, po, ref, setRef, msg, busy, resolver, choosePo, add, onCode, book, allAsOrdered, setQty, rows }
}

// ── Quick adjust ──────────────────────────────────────────────────────────────

export function useQuickAdjust(tenantId, products) {
  const [hit, setHit] = useState(null)
  const [qty, setQty] = useState(null)
  const [moves, setMoves] = useState([])
  const [val, setVal] = useState('')
  const [msg, setMsg] = useState('')
  const resolver = useResolver(tenantId, products)
  const { resolve } = resolver

  const show = useCallback(async (h) => {
    if (!h?.product) return
    setHit(h); setVal(''); setMsg('')
    setQty((h.variant || h.product).stock_quantity ?? null)
    const v = h.variant?.id ? `?variant_id=${h.variant.id}` : ''
    api(tenantId, `/products/${h.product.id}/stock-movements${v}`).then(d => setMoves(d.movements || [])).catch(() => setMoves([]))
  }, [tenantId])
  const onCode = useCallback(async (code) => {
    try { await show(await resolve(code)) } catch (e) { setMsg(e.message) }
  }, [resolve, show])

  async function save(kind) {
    const n = toQty(val)
    if (isNaN(n)) { setMsg('Type a number first.'); return }
    try {
      const r = await api(tenantId, '/stock/adjust', { method: 'POST', body: JSON.stringify({
        product_id: hit.product.id, variant_id: hit.variant?.id || null, ...(kind === 'set' ? { set: n } : { add: kind === 'add' ? n : -n }) }) })
      setQty(r.stock_quantity); setVal(''); setMsg(`Saved — stock is now ${r.stock_quantity}.`)
      const v = hit.variant?.id ? `?variant_id=${hit.variant.id}` : ''
      api(tenantId, `/products/${hit.product.id}/stock-movements${v}`).then(d => setMoves(d.movements || [])).catch(() => {})
    } catch (e) { setMsg(e.message) }
  }
  return { hit, qty, moves, val, setVal, msg, resolver, show, onCode, save }
}

/** The tenant's products, refreshed when the mode changes (stock moves between modes). */
export function useStockProducts(tenantId, initial, mode) {
  const [products, setProducts] = useState(initial)
  useEffect(() => {
    if (!tenantId) return
    api(tenantId, '/products').then(d => setProducts(d.products || [])).catch(() => {})
  }, [tenantId, mode])
  return products
}
