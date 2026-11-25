/**
 * useTill.js — the Till's data and actions (24 Nov 2026). Backend: vula_mind/vula/api/till.py.
 *
 * The till sends product ids and quantities, never prices. The basket's running total here is a
 * preview from the prices the server listed; the amount charged, and the change for cash, are the
 * server's and come back with the sale. Each basket carries a `saleId` made up here, so a double
 * tap or a retry on bad signal is one sale, not two — a new id is made only after a sale succeeds
 * or the basket is cleared.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

const base = (tenantId) => `${VULA_API}/v1/commerce/${tenantId}/admin/till`
const newSaleId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}-till`)
export const lineKey = (pid, vid) => `${pid}:${vid || ''}`

async function call(url, opts = {}) {
  const r = await fetch(url, opts.body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts.body) }
    : opts)
  const d = await r.json().catch(() => ({}))
  if (r.ok) return d
  const detail = d?.detail
  const err = new Error((typeof detail === 'string' ? detail : detail?.message) || 'That didn’t work. Please try again.')
  err.status = r.status
  throw err
}

/** A line's preview amount: the same rounding the server uses (quantity × unit price, to the cent). */
export const lineCents = (l) => Math.round(l.quantity * l.price_cents)

export function useTill(tenantId) {
  const [products, setProducts] = useState(null)
  const [methods, setMethods] = useState([])
  const [loadError, setLoadError] = useState('')
  const [lines, setLines] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState(null)
  const [summary, setSummary] = useState(null)
  const saleId = useRef(newSaleId())

  const load = useCallback(async () => {
    setLoadError('')
    try {
      const d = await call(`${base(tenantId)}/products`)
      setProducts(d.products || []); setMethods(d.methods || [])
    } catch (e) { setLoadError(e.message); setProducts([]) }
  }, [tenantId])
  const loadSummary = useCallback(async () => {
    // an answer missing a list is an empty list, not a crash (24 Nov 2026)
    try { const d = await call(`${base(tenantId)}/summary`); setSummary(d ? { sales: 0, net_cents: 0, by_method: [], ...d } : null) } catch { /* the takings panel just stays as it was */ }
  }, [tenantId])
  useEffect(() => { load(); loadSummary() }, [load, loadSummary])

  /** Every barcode and SKU the till knows → what it adds. Resolved here, no network per scan. */
  const byCode = useMemo(() => {
    const m = new Map()
    for (const p of products || []) {
      for (const c of [p.barcode, p.sku]) if (c) m.set(String(c).trim().toLowerCase(), { product: p, variant: null })
      for (const v of p.variants || []) for (const c of [v.barcode, v.sku]) if (c) m.set(String(c).trim().toLowerCase(), { product: p, variant: v })
    }
    return m
  }, [products])

  const add = useCallback((product, variant = null, quantity = 1) => {
    setError('')
    setLines((old) => {
      const key = lineKey(product.id, variant?.id)
      const hit = old.find((l) => l.key === key)
      if (hit) return old.map((l) => (l.key === key ? { ...l, quantity: l.quantity + quantity } : l))
      return [...old, {
        key, product_id: product.id, variant_id: variant?.id || null,
        name: variant ? `${product.name} — ${variant.label}` : product.name,
        price_cents: variant ? variant.price_cents : product.price_cents, by_kg: !!product.by_kg, quantity,
      }]
    })
  }, [])

  /** A scanned or typed code. Returns 'added', 'choose' (the product has options — pick one) or 'unknown'. */
  const scan = useCallback((code) => {
    const hit = byCode.get(String(code || '').trim().toLowerCase())
    if (!hit) return { result: 'unknown' }
    if (!hit.variant && (hit.product.variants || []).length > 0) return { result: 'choose', product: hit.product }
    add(hit.product, hit.variant)
    return { result: 'added', product: hit.product }
  }, [byCode, add])

  const setQuantity = (key, quantity) => setLines((old) => old
    .map((l) => (l.key === key ? { ...l, quantity } : l)).filter((l) => l.quantity > 0))
  const remove = (key) => setLines((old) => old.filter((l) => l.key !== key))
  const clear = () => { setLines([]); setError(''); saleId.current = newSaleId() }

  const previewCents = useMemo(() => lines.reduce((sum, l) => sum + lineCents(l), 0), [lines])
  const count = useMemo(() => lines.reduce((n, l) => n + (l.by_kg ? 1 : l.quantity), 0), [lines])

  /** Take the money. `extra`: {tendered_cents, customer_name, customer_phone, discount_code}. */
  const charge = async (method, extra = {}) => {
    setBusy(true); setError('')
    try {
      const d = await call(`${base(tenantId)}/sales`, { body: {
        sale_id: saleId.current, method, ...extra,
        items: lines.map((l) => ({ product_id: l.product_id, variant_id: l.variant_id, quantity: l.quantity })),
      } })
      setReceipt(d.sale)
      setLines([])
      saleId.current = newSaleId()
      loadSummary()
      load()                      // stock moved
      return d.sale
    } catch (e) {
      setError(e.message)
      return null
    } finally { setBusy(false) }
  }

  return {
    products, methods, loadError, load, lines, add, scan, setQuantity, remove, clear, previewCents, count,
    busy, error, setError, charge, receipt, setReceipt, summary, loadSummary,
  }
}
