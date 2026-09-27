/**
 * VulaStock.jsx — the Stock tab: count, receive and adjust stock by scanning barcodes with the
 * phone camera.
 *
 *   📋 Stock-take  → start a count, scan every item (each scan adds one, or type a number),
 *                    review counted vs system with the difference in rands, owner/manager applies
 *   📦 Receive     → pick a purchase order (or none), scan what arrived, book it into stock
 *   ✏️ Quick adjust → scan one item, see its stock and history, set or add to it
 *
 * Camera: the browser's BarcodeDetector (Android Chrome) or, where it's missing (iPhone
 * Safari), @zxing/browser loaded on first use. A barcode nothing carries can be linked to a
 * product on the spot, so a shop builds its barcode list just by scanning.
 *
 * Stock-take scans are queued in this browser and sent in batches, so a count keeps going with
 * no signal in the storeroom. Each scan carries its own id and the server ignores one it has
 * seen, so a batch re-sent after a dropped reply never double-counts (migration 183).
 * Every change is recorded as a stock movement (migration 182).
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { VULA_API } from '../lib/authFetch'
import { useAuthStore } from '../store/auth'
import { Card, Button, Badge, SectionTabs, EmptyState, inputStyle } from './ui'
import { T } from '../theme/tokens'

const H = { 'Content-Type': 'application/json' }   // JWT attached by lib/authFetch

async function api(tenantId, path, opts = {}) {
  const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin${path}`, { headers: H, ...opts })
  let d = {}
  try { d = await r.json() } catch { /* empty body */ }
  if (!r.ok) { const e = new Error(d.detail || `Request failed (${r.status})`); e.status = r.status; throw e }
  return d
}

const rands = (cents) => cents == null ? '—' : `${cents < 0 ? '−' : ''}R${(Math.abs(cents) / 100).toFixed(2)}`
const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`)
const itemKey = (pid, vid) => `${pid}:${vid || ''}`
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

// ── Camera barcode scanner ────────────────────────────────────────────────────

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code']

function BarcodeScanner({ onCode, paused }) {
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

  return (
    <Card pad={12} style={{ marginBottom: 12 }}>
      <div style={{ position: 'relative', background: '#000', borderRadius: 8, overflow: 'hidden',
                    aspectRatio: '4 / 3', maxHeight: 320, display: on ? 'block' : 'none' }}>
        <video ref={videoRef} muted playsInline style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        <div style={{ position: 'absolute', left: '10%', right: '10%', top: '45%', height: 2, background: 'rgba(255,60,60,.8)' }} />
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: on ? 10 : 0, flexWrap: 'wrap' }}>
        <Button size="sm" variant={on ? 'ghost' : 'primary'} onClick={() => setOn(v => !v)}>
          {on ? 'Stop camera' : '📷 Start camera'}
        </Button>
        <form style={{ display: 'flex', gap: 6, flex: 1, minWidth: 200 }}
              onSubmit={(e) => { e.preventDefault(); if (manual.trim()) { onCode(manual.trim()); setManual('') } }}>
          <input value={manual} onChange={e => setManual(e.target.value)} placeholder="…or type / use a USB scanner"
                 inputMode="numeric" style={{ ...inputStyle, padding: '6px 10px' }} />
          <Button size="sm" variant="soft" type="submit">Add</Button>
        </form>
      </div>
      {err && <div style={{ color: T.danger, fontSize: 12.5, marginTop: 8 }}>{err}</div>}
    </Card>
  )
}

// ── Product search + barcode resolution ───────────────────────────────────────

function ProductPicker({ products, onPick, placeholder = 'Search products…' }) {
  const [q, setQ] = useState('')
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return []
    return products.filter(p => !p.archived && `${p.name} ${p.sku || ''} ${p.barcode || ''}`.toLowerCase().includes(s)).slice(0, 8)
  }, [q, products])
  return (
    <div>
      <input value={q} onChange={e => setQ(e.target.value)} placeholder={placeholder} style={inputStyle} />
      {hits.map(p => (
        <div key={p.id} onClick={() => { onPick(p); setQ('') }}
             style={{ padding: '8px 10px', borderBottom: `1px solid ${T.border}`, cursor: 'pointer', fontSize: 13.5 }}>
          {p.name} <span style={{ color: T.muted }}>· stock {p.stock_quantity ?? 'not tracked'}</span>
        </div>
      ))}
    </div>
  )
}

/** Turns a scanned code into {product, variant}; asks to link a code nothing carries. */
function useResolver(tenantId, products) {
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

  const linker = unknown && (
    <Card pad={14} style={{ marginBottom: 12, borderColor: T.warn }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Barcode {unknown.code} isn't on any product yet</div>
      <div style={{ fontSize: 12.5, color: T.muted, marginBottom: 8 }}>Pick the product it belongs to — next time it scans straight in.</div>
      <ProductPicker products={products} onPick={async (p) => {
        try {
          const hit = await api(tenantId, `/products/${p.id}/barcode`, { method: 'POST', body: JSON.stringify({ barcode: unknown.code }) })
          cache.current[unknown.code] = hit
          unknown.resolve(hit)
        } catch (e) { alert(e.message); unknown.resolve(null) }
        setUnknown(null)
      }} />
      <Button size="sm" variant="ghost" style={{ marginTop: 8 }} onClick={() => { unknown.resolve(null); setUnknown(null) }}>Skip</Button>
    </Card>
  )
  return { resolve, linker, waiting: !!unknown }
}

const itemName = (hit) => {
  const base = hit?.product?.name || 'Unknown product'
  const opts = hit?.variant?.option_values
  return opts && typeof opts === 'object' ? `${base} — ${Object.values(opts).join(' / ')}` : base
}

// ── Stock-take ────────────────────────────────────────────────────────────────

function StockTake({ tenantId, products }) {
  const full = useAuthStore(s => s.full)
  const [counts, setCounts] = useState([])
  const [countId, setCountId] = useState(null)
  const [note, setNote] = useState('')
  const [review, setReview] = useState(null)
  const [msg, setMsg] = useState('')
  const load = useCallback(() => api(tenantId, '/stock/counts').then(d => setCounts(d.counts || [])).catch(e => setMsg(e.message)), [tenantId])
  useEffect(() => { load() }, [load])

  if (countId) return <CountSession tenantId={tenantId} products={products} countId={countId} full={full}
                                     review={review} setReview={setReview}
                                     onClose={() => { setCountId(null); setReview(null); load() }} />
  const open = counts.filter(c => c.status === 'open')
  return (
    <div>
      <Card pad={14} style={{ marginBottom: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Start a stock-take</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="Name it, e.g. Month end — cold room" style={inputStyle} />
          <Button onClick={async () => {
            try { const c = await api(tenantId, '/stock/counts', { method: 'POST', body: JSON.stringify({ note }) }); setNote(''); setCountId(c.id) }
            catch (e) { setMsg(e.message) }
          }}>Start</Button>
        </div>
        <div style={{ fontSize: 12, color: T.muted, marginTop: 6 }}>Several people can count into the same stock-take from their own phones.</div>
      </Card>
      {msg && <div style={{ color: T.danger, marginBottom: 8 }}>{msg}</div>}
      {counts.length === 0 ? <EmptyState icon="📋" title="No stock-takes yet">Start one above and scan your shelves.</EmptyState> :
        counts.map(c => (
          <Card key={c.id} pad={12} style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 600 }}>{c.note || 'Stock-take'}</div>
              <div style={{ fontSize: 12, color: T.muted }}>{new Date(c.created_at).toLocaleString()} · {c.started_by || ''}</div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Badge tone={c.status === 'open' ? 'accent' : c.status === 'applied' ? 'ok' : 'muted'}>{c.status}</Badge>
              <Button size="sm" variant="soft" onClick={() => setCountId(c.id)}>{c.status === 'open' ? 'Continue' : 'View'}</Button>
            </div>
          </Card>
        ))}
      {open.length > 1 && <div style={{ fontSize: 12, color: T.muted }}>{open.length} stock-takes are still open.</div>}
    </div>
  )
}

function CountSession({ tenantId, products, countId, full, review, setReview, onClose }) {
  const qKey = `vula.stockq.${tenantId}.${countId}`
  const tKey = `vula.stockt.${tenantId}.${countId}`
  const [queue, setQueue] = useState(() => loadJSON(qKey, []))
  const [tally, setTally] = useState(() => loadJSON(tKey, {}))   // itemKey → {name, counted}
  const [status, setStatus] = useState('open')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const syncing = useRef(false)
  const { resolve, linker, waiting } = useResolver(tenantId, products)

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
    if (!confirm('Set stock to these counts? Items you did not count are left as they are.')) return
    setBusy(true)
    try {
      const r = await api(tenantId, `/stock/counts/${countId}/apply`, { method: 'POST' })
      setMsg(`Applied ${r.applied} item${r.applied === 1 ? '' : 's'}${r.failed.length ? ` — ${r.failed.length} could not be updated` : ''}.`)
      saveJSON(qKey, []); saveJSON(tKey, {})
      await openReview()
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }

  const lines = Object.entries(tally).sort((a, b) => a[1].name.localeCompare(b[1].name))
  if (review) {
    return (
      <div>
        <Button size="sm" variant="ghost" onClick={() => setReview(null)} style={{ marginBottom: 10 }}>← Back to scanning</Button>
        <Card pad={14} style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <div>
              <div style={{ fontWeight: 700 }}>{review.count.note || 'Stock-take'} <Badge tone={review.count.status === 'applied' ? 'ok' : 'accent'}>{review.count.status}</Badge></div>
              <div style={{ fontSize: 12.5, color: T.muted }}>{review.lines.length} items counted · difference at cost {rands(review.variance_cents)}
                {review.lines_without_cost > 0 && ` (${review.lines_without_cost} with no cost price)`}</div>
            </div>
            {review.count.status === 'open' && (full
              ? <div style={{ display: 'flex', gap: 8 }}>
                  <Button variant="danger" size="sm" disabled={busy} onClick={async () => {
                    if (!confirm('Cancel this stock-take? Nothing will change.')) return
                    try { await api(tenantId, `/stock/counts/${countId}/cancel`, { method: 'POST' }); onClose() } catch (e) { setMsg(e.message) }
                  }}>Cancel count</Button>
                  <Button disabled={busy || !review.lines.length} onClick={applyCount}>Apply to stock</Button>
                </div>
              : <Badge tone="info">The owner or a manager applies this count</Badge>)}
          </div>
        </Card>
        {msg && <div style={{ marginBottom: 8, color: T.muted }}>{msg}</div>}
        <Card pad={0} style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ textAlign: 'left', color: T.muted }}>
              {['Item', 'System', 'Counted', 'Difference', 'Value'].map(h => <th key={h} style={{ padding: '8px 10px', borderBottom: `1px solid ${T.border}` }}>{h}</th>)}
            </tr></thead>
            <tbody>{review.lines.map(l => (
              <tr key={itemKey(l.product_id, l.variant_id)} style={{ borderBottom: `1px solid ${T.border}` }}>
                <td style={{ padding: '8px 10px' }}>{l.name}</td>
                <td style={{ padding: '8px 10px' }}>{l.expected ?? 'not tracked'}</td>
                <td style={{ padding: '8px 10px', fontWeight: 600 }}>{l.counted}</td>
                <td style={{ padding: '8px 10px', color: l.variance < 0 ? T.danger : l.variance > 0 ? T.ok : T.muted }}>
                  {l.variance > 0 ? '+' : ''}{l.variance}</td>
                <td style={{ padding: '8px 10px' }}>{rands(l.variance_cents)}</td>
              </tr>))}
            </tbody>
          </table>
        </Card>
        {review.count.status === 'open' && review.uncounted.length > 0 && (
          <Card pad={12} style={{ marginTop: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Not counted yet ({review.uncounted.length})</div>
            <div style={{ fontSize: 12.5, color: T.muted }}>These keep their current stock unless you count them:
              {' '}{review.uncounted.slice(0, 30).map(u => `${u.name} (${u.expected})`).join(', ')}{review.uncounted.length > 30 ? '…' : ''}</div>
          </Card>)}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <Button size="sm" variant="ghost" onClick={onClose}>← All stock-takes</Button>
        <div style={{ fontSize: 12, color: queue.length ? T.warn : T.muted }}>
          {queue.length ? `${queue.length} scan${queue.length === 1 ? '' : 's'} waiting to send${navigator.onLine ? '…' : ' (offline)'}` : 'All scans saved'}
        </div>
      </div>
      {status !== 'open' ? <Card pad={14}>This stock-take is {status}. <Button size="sm" variant="soft" onClick={openReview}>View</Button></Card> : <>
        {linker}
        <BarcodeScanner onCode={onCode} paused={waiting} />
        <Card pad={12} style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: T.muted, marginBottom: 6 }}>No barcode? Find it by name — each pick adds one.</div>
          <ProductPicker products={products} onPick={(p) => record({ product: p, variant: null })} />
        </Card>
        {lines.length > 0 && (
          <Card pad={0} style={{ marginBottom: 12 }}>
            {lines.map(([k, l]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${T.border}` }}>
                <div style={{ flex: 1, fontSize: 13.5 }}>{l.name}</div>
                <input type="number" min="0" value={l.counted} style={{ ...inputStyle, width: 80, padding: '5px 8px' }}
                       onChange={e => { const v = parseInt(e.target.value, 10); if (!isNaN(v) && v >= 0) record({ product: { id: l.pid, name: l.name }, variant: l.vid ? { id: l.vid } : null }, { set: v }) }} />
              </div>))}
          </Card>)}
        <Button disabled={busy} onClick={openReview}>Review count</Button>
        {msg && <div style={{ marginTop: 8, color: T.danger }}>{msg}</div>}
      </>}
    </div>
  )
}

// ── Receive a delivery ────────────────────────────────────────────────────────

function Receive({ tenantId, products }) {
  const [pos, setPos] = useState([])
  const [po, setPo] = useState(null)
  const [lines, setLines] = useState({})   // itemKey → {pid, vid, name, qty, ordered, cost}
  const [ref, setRef] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const { resolve, linker, waiting } = useResolver(tenantId, products)
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

  const rows = Object.entries(lines)
  return (
    <div>
      <Card pad={14} style={{ marginBottom: 12, display: 'grid', gap: 8 }}>
        <select value={po?.id || ''} onChange={e => choosePo(e.target.value)} style={inputStyle}>
          <option value="">No purchase order — just book what arrived</option>
          {pos.map(p => <option key={p.id} value={p.id}>{p.po_number || p.id.slice(0, 8)} · {p.supplier_name || 'supplier'} · {p.status}</option>)}
        </select>
        <input value={ref} onChange={e => setRef(e.target.value)} placeholder="Delivery note / invoice number (optional)" style={inputStyle} />
        <div style={{ fontSize: 12, color: T.muted }}>Have the paper delivery note? Money › Scanner reads it and matches the lines for you.</div>
      </Card>
      {linker}
      <BarcodeScanner onCode={onCode} paused={waiting} />
      <Card pad={12} style={{ marginBottom: 12 }}>
        <ProductPicker products={products} onPick={(p) => add({ product: p, variant: null })} placeholder="Add by name…" />
      </Card>
      {rows.length > 0 && (
        <Card pad={0} style={{ marginBottom: 12 }}>
          {po && <div style={{ padding: '8px 12px', textAlign: 'right' }}>
            <Button size="sm" variant="ghost" onClick={() => setLines(ls => Object.fromEntries(Object.entries(ls).map(([k, l]) => [k, { ...l, qty: l.ordered ?? l.qty }])))}>
              Everything arrived as ordered</Button></div>}
          {rows.map(([k, l]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${T.border}` }}>
              <div style={{ flex: 1, fontSize: 13.5 }}>{l.name}{l.ordered != null && <span style={{ color: T.muted }}> · ordered {l.ordered}</span>}</div>
              <input type="number" min="0" value={l.qty} style={{ ...inputStyle, width: 80, padding: '5px 8px' }}
                     onChange={e => { const v = Math.max(0, parseInt(e.target.value, 10) || 0); setLines(ls => ({ ...ls, [k]: { ...ls[k], qty: v } })) }} />
            </div>))}
        </Card>)}
      <Button disabled={busy} onClick={book}>Book into stock</Button>
      {msg && <div style={{ marginTop: 8, color: T.muted }}>{msg}</div>}
    </div>
  )
}

// ── Quick adjust ──────────────────────────────────────────────────────────────

const REASON = { count: 'Stock-take', receive: 'Received', adjust: 'Adjusted', sale: 'Sold', refund: 'Refunded', cancel: 'Cancelled', import: 'Imported' }

function QuickAdjust({ tenantId, products }) {
  const [hit, setHit] = useState(null)
  const [qty, setQty] = useState(null)
  const [moves, setMoves] = useState([])
  const [val, setVal] = useState('')
  const [msg, setMsg] = useState('')
  const { resolve, linker, waiting } = useResolver(tenantId, products)

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
    const n = parseInt(val, 10)
    if (isNaN(n)) { setMsg('Type a number first.'); return }
    try {
      const r = await api(tenantId, '/stock/adjust', { method: 'POST', body: JSON.stringify({
        product_id: hit.product.id, variant_id: hit.variant?.id || null, ...(kind === 'set' ? { set: n } : { add: kind === 'add' ? n : -n }) }) })
      setQty(r.stock_quantity); setVal(''); setMsg(`Saved — stock is now ${r.stock_quantity}.`)
      const v = hit.variant?.id ? `?variant_id=${hit.variant.id}` : ''
      api(tenantId, `/products/${hit.product.id}/stock-movements${v}`).then(d => setMoves(d.movements || [])).catch(() => {})
    } catch (e) { setMsg(e.message) }
  }

  return (
    <div>
      {linker}
      <BarcodeScanner onCode={onCode} paused={waiting} />
      <Card pad={12} style={{ marginBottom: 12 }}>
        <ProductPicker products={products} onPick={(p) => show({ product: p, variant: null })} placeholder="…or find by name" />
      </Card>
      {hit && (
        <Card pad={14}>
          <div style={{ fontWeight: 700, fontSize: 16 }}>{itemName(hit)}</div>
          <div style={{ fontSize: 13, color: T.muted, marginBottom: 10 }}>In stock: <b style={{ color: T.ink }}>{qty ?? 'not tracked'}</b></div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <input type="number" min="0" value={val} onChange={e => setVal(e.target.value)} placeholder="Qty" style={{ ...inputStyle, width: 90 }} />
            <Button size="sm" onClick={() => save('set')}>Set to</Button>
            <Button size="sm" variant="soft" onClick={() => save('add')}>+ Add</Button>
            <Button size="sm" variant="ghost" onClick={() => save('remove')}>− Remove</Button>
          </div>
          {msg && <div style={{ marginTop: 8, fontSize: 13, color: T.muted }}>{msg}</div>}
          {moves.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.08em', color: T.muted, marginBottom: 4 }}>History</div>
              {moves.map(m => (
                <div key={m.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, padding: '4px 0', borderBottom: `1px solid ${T.border}` }}>
                  <span>{REASON[m.reason] || m.reason} <span style={{ color: m.delta < 0 ? T.danger : T.ok }}>{m.delta > 0 ? '+' : ''}{m.delta}</span>
                    {m.qty_after != null && <span style={{ color: T.muted }}> → {m.qty_after}</span>}</span>
                  <span style={{ color: T.muted }}>{m.actor || ''} · {new Date(m.created_at).toLocaleDateString()}</span>
                </div>))}
            </div>)}
        </Card>)}
      {!hit && msg && <div style={{ color: T.danger }}>{msg}</div>}
    </div>
  )
}

// ── Tab ───────────────────────────────────────────────────────────────────────

const MODES = [
  { id: 'count', icon: '📋', label: 'Stock-take' },
  { id: 'receive', icon: '📦', label: 'Receive' },
  { id: 'adjust', icon: '✏️', label: 'Quick adjust' },
]

export default function VulaStock({ tenantId, products: initial = [] }) {
  const [mode, setMode] = useState('count')
  const [products, setProducts] = useState(initial)
  useEffect(() => {
    if (!tenantId) return
    api(tenantId, '/products').then(d => setProducts(d.products || [])).catch(() => {})
  }, [tenantId, mode])
  if (!tenantId) return <EmptyState icon="📦" title="Choose a business first" />
  return (
    <div style={{ maxWidth: 760 }}>
      <SectionTabs tabs={MODES} active={mode} onChange={setMode} />
      {mode === 'count' && <StockTake tenantId={tenantId} products={products} />}
      {mode === 'receive' && <Receive tenantId={tenantId} products={products} />}
      {mode === 'adjust' && <QuickAdjust tenantId={tenantId} products={products} />}
    </div>
  )
}
