/**
 * useSmartScanner.js — photograph a business document, let the server read it, review, then one
 * tap books it: commit to books (expense / bill with supplier terms), receive matched lines into
 * stock, or update cost prices (2026-10-19, UX redesign Phase F). Moved out of VulaSmartScanner
 * unchanged — same /admin/scan, /scan/commit, /scan/match-lines, /stock/receive, /products/costs
 * and /suppliers calls. Amounts arrive as integer cents from the server; nothing is computed here.
 */
import { useEffect, useRef, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

const H = { 'Content-Type': 'application/json' }  // JWT attached by lib/authFetch

export function useSmartScanner({ tenantId, onExpenseCreated, onStockUpdated }) {
  const [docType, setDocType] = useState('auto')
  const [preview, setPreview] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [result, setResult] = useState(null)
  const [commitPreview, setCommitPreview] = useState(null)   // preview from /scan/commit
  const [committed, setCommitted] = useState(null)           // committed record
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [suppliers, setSuppliers] = useState([])
  const [lineMatches, setLineMatches] = useState(null)       // server-matched line items
  const [stockMsg, setStockMsg] = useState('')
  const fileRef = useRef(null)

  // Load known suppliers for payment-terms hints
  useEffect(() => {
    if (!tenantId) return
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers`, { headers: H })
      .then(r => r.json()).then(d => setSuppliers(d.suppliers || [])).catch(() => {})
  }, [tenantId])

  async function handleImage(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null); setResult(null); setSaved(false)

    // Read as base64
    const reader = new FileReader()
    reader.onload = async () => {
      const dataUrl = reader.result
      setPreview(dataUrl)
      await runScan(dataUrl)
    }
    reader.readAsDataURL(file)
  }

  async function runScan(dataUrl) {
    setScanning(true)
    setError(null)
    setCommitPreview(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/scan`, {
        method: 'POST',
        headers: H,
        body: JSON.stringify({ image_base64: dataUrl, doc_type: docType, tenant_id: tenantId }),
      })
      const d = await r.json()
      if (!d.ok) throw new Error(d.detail || 'Scan failed')
      setResult(d.extracted)
      loadLineMatches(d.extracted)

      // Auto-preview the payment terms / due date from commit endpoint
      if (d.extracted?.total_cents > 0) {
        const prevR = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/scan/commit`, {
          method: 'POST', headers: H,
          body: JSON.stringify({ extracted: d.extracted, auto_commit: false }),
        })
        if (prevR.ok) setCommitPreview((await prevR.json()).preview)
      }
    } catch (err) {
      setError(err.message || 'Could not scan document')
    } finally {
      setScanning(false)
    }
  }

  // ── Commit to books (smart: supplier match → payment terms → due date) ────────

  async function commitToBooks() {
    setSaving(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/scan/commit`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ extracted: result, auto_commit: true }),
      })
      const d = await r.json()
      if (!d.ok) throw new Error(d.detail || 'Commit failed')
      setCommitted(d)
      setSaved(true)
      onExpenseCreated?.()
    } catch (err) {
      setError(err.message || 'Could not save to books')
    } finally {
      setSaving(false)
    }
  }

  // ── Legacy: manual expense save (fallback) ────────────────────────────────────

  async function saveAsExpense() {
    return commitToBooks()  // redirect to smart commit
  }

  // Lines matched to products on the server (the WhatsApp assistant's matcher) — replaced an
  // in-browser "first 6 letters" match that PATCHed stock_quantity = old + qty per line: it
  // matched the wrong product, lost concurrent changes, ignored errors and variants.
  async function loadLineMatches(extracted) {
    setLineMatches(null)
    const lines = extracted?.line_items || []
    if (!lines.length) return
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/scan/match-lines`, {
        method: 'POST', headers: H, body: JSON.stringify({ lines }) })
      const d = await r.json()
      setLineMatches((d.lines || []).map(l => ({ ...l, product_id: l.match?.product_id || '',
        qty: Math.max(0, Math.round((Number(l.quantity) || 0) * 10000) / 10000), include: !!l.match })))
    } catch { setLineMatches([]) }
  }

  const updateLine = (i, patch) => setLineMatches(ls => ls.map((l, j) => j === i ? { ...l, ...patch } : l))

  async function bookStock() {
    const lines = (lineMatches || []).filter(l => l.include && l.product_id && l.qty > 0)
      .map(l => ({ product_id: l.product_id, quantity: l.qty,
                   ...(l.unit_price_cents ? { unit_cost_cents: l.unit_price_cents } : {}) }))
    if (!lines.length) { setError('Tick at least one matched line with a quantity.'); return }
    setSaving(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/stock/receive`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ lines, reference: result?.reference || result?.supplier || null }) })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Could not book stock')
      setStockMsg(`📦 Booked ${d.received} line${d.received === 1 ? '' : 's'} into stock${d.failed?.length ? ` — ${d.failed.length} failed` : ''}.`)
      setLineMatches(null)
      onStockUpdated?.()
    } catch (err) { setError(err.message || 'Could not update stock') } finally { setSaving(false) }
  }

  async function updateCosts() {
    const costs = (lineMatches || []).filter(l => l.include && l.product_id && l.unit_price_cents > 0)
      .map(l => ({ product_id: l.product_id, cost_cents: l.unit_price_cents }))
    if (!costs.length) { setError('No ticked line has both a product and a unit price.'); return }
    setSaving(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/costs`, {
        method: 'POST', headers: H, body: JSON.stringify({ costs }) })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Could not update cost prices')
      setStockMsg(`💲 Updated the cost price of ${d.updated} product${d.updated === 1 ? '' : 's'}.`)
      setLineMatches(null)
    } catch (err) { setError(err.message) } finally { setSaving(false) }
  }

  function reset() {
    setPreview(null); setResult(null); setError(null); setSaved(false); setLineMatches(null); setStockMsg('')
    if (fileRef.current) fileRef.current.value = ''
  }

  return { docType, setDocType, preview, scanning, result, commitPreview, committed, error, saving, saved, lineMatches, stockMsg, fileRef, updateLine, handleImage, commitToBooks, bookStock, updateCosts, reset }
}
