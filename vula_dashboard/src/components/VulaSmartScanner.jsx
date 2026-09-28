/**
 * VulaSmartScanner.jsx — AI Smart Scanner
 *
 * The flagship workflow-simplifier: photograph ANY business document and
 * AI extracts structured data, then one tap turns it into an action.
 *
 *   📷 Snap a supplier receipt  → auto-creates an EXPENSE
 *   📦 Snap a delivery note     → lines matched to products on the server, reviewed, then
 *                                 booked into STOCK (recorded as 'receive' movements)
 *   💲 Snap a supplier price list → proposes COST PRICE updates for matched products
 *   🧾 Snap an invoice           → pre-fills an INVOICE
 *   📝 Snap a handwritten order  → pre-fills an ORDER
 *
 * Flow: capture/upload photo → POST /admin/scan → review extracted JSON →
 * confirm → POST to the relevant endpoint (expense / product stock / invoice).
 */

import { useState, useRef, useEffect } from 'react'
import { VULA_API } from '../lib/authFetch'

const H = { 'Content-Type': 'application/json' }  // JWT attached by lib/authFetch

const DOC_TYPES = [
  { id: 'auto',          label: '✨ Auto-detect',     hint: 'Let AI figure it out' },
  { id: 'receipt',       label: '🧾 Supplier receipt', hint: '→ creates an expense' },
  { id: 'delivery_note', label: '📦 Delivery note',    hint: '→ updates stock' },
  { id: 'invoice',       label: '📄 Invoice',          hint: '→ pre-fills invoice' },
  { id: 'price_list',    label: '💲 Price list',       hint: '→ updates cost prices' },
]

export default function VulaSmartScanner({ tenantId, products = [], onExpenseCreated, onStockUpdated }) {
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
        qty: Math.max(0, Math.round(Number(l.quantity) || 0)), include: !!l.match })))
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

  const fmt = c => c != null ? `R${(c / 100).toFixed(2)}` : '—'
  const detectedType = result?.doc_type || docType

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>📸 Smart Scanner</h3>
        <p style={s.sub}>
          Snap a photo of any receipt, delivery note, or invoice — AI reads it and
          turns it into an expense or stock update in one tap.
        </p>
      </div>

      {/* Doc type selector */}
      <div style={s.chips}>
        {DOC_TYPES.map(d => (
          <button
            key={d.id}
            onClick={() => setDocType(d.id)}
            title={d.hint}
            style={{ ...s.chip, ...(docType === d.id ? s.chipActive : {}) }}
          >
            {d.label}
          </button>
        ))}
      </div>

      {/* Capture */}
      {!preview && (
        <div>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            capture="environment"
            onChange={handleImage}
            style={{ display: 'none' }}
            id="scan-input"
          />
          <label htmlFor="scan-input" style={s.captureBtn}>
            📷 Take photo or upload
          </label>
          <p style={s.hint}>{DOC_TYPES.find(d => d.id === docType)?.hint}</p>
        </div>
      )}

      {/* Preview + scanning */}
      {preview && (
        <div style={s.previewWrap}>
          <img src={preview} alt="Scanned document" style={s.previewImg} />
          {scanning && <div style={s.scanningOverlay}>🔍 AI reading document…</div>}
          <button onClick={reset} style={s.retakeBtn}>↺ Retake</button>
        </div>
      )}

      {error && <div style={s.errorBox}>{error}</div>}

      {/* Extracted result */}
      {result && !saved && (
        <div style={s.resultCard}>
          <div style={s.resultHeader}>
            <span style={s.detectedBadge}>
              Detected: {detectedType} · {result.confidence || 'medium'} confidence
            </span>
          </div>

          {/* Summary fields */}
          <div style={s.fieldGrid}>
            {result.supplier && <Field label="Supplier" value={result.supplier} />}
            {result.customer && <Field label="Customer" value={result.customer} />}
            {result.date && <Field label="Date" value={result.date} />}
            {result.category && <Field label="Category" value={result.category} />}
            {result.total_cents != null && <Field label="Total" value={fmt(result.total_cents)} accent />}
          </div>

          {/* Line items */}
          {result.line_items?.length > 0 && (
            <div style={s.lineItems}>
              <p style={s.lineHeader}>Line items ({result.line_items.length})</p>
              {result.line_items.map((it, i) => (
                <div key={i} style={s.lineRow}>
                  <span>{it.description}</span>
                  <span style={s.lineQty}>
                    {it.quantity}{it.unit ? ` ${it.unit}` : ''} {it.total_cents != null ? `· ${fmt(it.total_cents)}` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Payment terms + due date preview from scan/commit */}
          {commitPreview && !saved && (
            <div style={{
              margin: '12px 0', padding: '12px 16px',
              background: commitPreview.days_until_due < 0 ? '#FEF2F2'
                        : commitPreview.days_until_due <= 7 ? '#FFFBEB' : '#F0FDF4',
              border: `1px solid ${commitPreview.days_until_due < 0 ? '#FCA5A5'
                        : commitPreview.days_until_due <= 7 ? '#FCD34D' : '#86EFAC'}`,
              borderRadius: 8, fontSize: 13,
            }}>
              <strong>
                {commitPreview.days_until_due < 0 ? '⚠️ Overdue' :
                 commitPreview.days_until_due === 0 ? '🔴 Due today' :
                 commitPreview.days_until_due <= 7  ? '🟡 Due soon' : '✅ Payment terms'}
              </strong>
              <br />
              {commitPreview.supplier_known
                ? `Supplier "${commitPreview.supplier}" — ${commitPreview.payment_terms_days} day terms`
                : `New supplier — defaulting to ${commitPreview.payment_terms_days} day terms`}
              {commitPreview.supplier_match && (
                <span style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
                  🔗 Matched <strong>{commitPreview.supplier_match.supplier_name}</strong>{' '}
                  via {commitPreview.supplier_match.tier} ({Math.round((commitPreview.supplier_match.confidence || 0) * 100)}%)
                  {commitPreview.supplier_match.auto_applied
                    ? ' — terms applied automatically'
                    : ' — confirm to apply'}
                </span>
              )}
              {commitPreview.due_date && (
                <span style={{ fontWeight: 600 }}>
                  {' '}· Due {commitPreview.due_date}
                  {commitPreview.days_until_due != null && ` (${Math.abs(commitPreview.days_until_due)} days${commitPreview.days_until_due < 0 ? ' overdue' : ''})`}
                </span>
              )}
            </div>
          )}

          {/* Action buttons by detected type */}
          <div style={s.actions}>
            {detectedType !== 'price_list' && (detectedType === 'receipt' || detectedType === 'invoice' || detectedType === 'delivery_note' || result.total_cents != null) && (
              <button onClick={commitToBooks} disabled={saving} style={s.btnPrimary}>
                {saving ? 'Saving to books…' : `📊 Commit to books${result.total_cents ? ` (${fmt(result.total_cents)})` : ''}`}
              </button>
            )}
            {lineMatches?.length > 0 && (
              <div style={s.lineItems}>
                <p style={s.lineHeader}>Match lines to your products ({lineMatches.filter(l => l.match).length}/{lineMatches.length} found)</p>
                {lineMatches.map((l, i) => (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '20px 1fr 64px', gap: 6, alignItems: 'center', padding: '4px 0', fontSize: 12, fontFamily: 'system-ui' }}>
                    <input type="checkbox" checked={l.include} onChange={e => updateLine(i, { include: e.target.checked })} />
                    <div>
                      <div style={{ color: '#8A8680' }}>{l.description}{l.unit_price_cents ? ` · ${fmt(l.unit_price_cents)} each` : ''}</div>
                      <select value={l.product_id} onChange={e => updateLine(i, { product_id: e.target.value, include: !!e.target.value })}
                              style={{ width: '100%', fontSize: 12, padding: 3 }}>
                        <option value="">— not in my products —</option>
                        {l.match && <option value={l.match.product_id}>{l.match.name}</option>}
                        {l.suggestions.map(p => <option key={p.product_id} value={p.product_id}>Did you mean: {p.name}</option>)}
                        {products.filter(p => !p.archived && p.id !== l.match?.product_id && !l.suggestions.some(x => x.product_id === p.id))
                          .map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </select>
                    </div>
                    {detectedType !== 'price_list' && (
                      <input type="number" min="0" value={l.qty} onChange={e => updateLine(i, { qty: Math.max(0, parseInt(e.target.value, 10) || 0) })}
                             style={{ width: 60, fontSize: 12, padding: 3 }} />)}
                  </div>
                ))}
              </div>
            )}
            {lineMatches?.length > 0 && detectedType !== 'price_list' && (
              <button onClick={bookStock} disabled={saving} style={s.btnSecondary}>
                {saving ? 'Booking…' : '📦 Book ticked lines into stock'}
              </button>
            )}
            {lineMatches?.some(l => l.unit_price_cents > 0) && (
              <button onClick={updateCosts} disabled={saving} style={s.btnSecondary}>
                {saving ? 'Updating…' : '💲 Update cost prices from this document'}
              </button>
            )}
            {stockMsg && <div style={{ fontSize: 13, fontFamily: 'system-ui', color: '#16a34a' }}>{stockMsg}</div>}
          </div>
        </div>
      )}

      {/* Saved confirmation */}
      {saved && committed && (
        <div style={s.savedCard}>
          <p style={{ ...s.savedText, fontSize: 14 }}>{committed.message}</p>
          <p style={{ fontSize: 12, color: '#6B7280', marginTop: 4 }}>
            {committed.kb_chunks_added > 0 && `📚 ${committed.kb_chunks_added} knowledge chunks added · `}
            Saved as {committed.record_type}
          </p>
          <button onClick={reset} style={{ ...s.btnPrimary, marginTop: 12 }}>Scan another</button>
        </div>
      )}
      {saved && !committed && (
        <div style={s.savedCard}>
          <p style={s.savedText}>✓ Saved successfully</p>
          <button onClick={reset} style={s.btnPrimary}>Scan another</button>
        </div>
      )}
    </div>
  )
}

function Field({ label, value, accent }) {
  return (
    <div style={s.field}>
      <span style={s.fieldLabel}>{label}</span>
      <span style={{ ...s.fieldValue, ...(accent ? { color: 'var(--accent, var(--accent))', fontWeight: 700 } : {}) }}>{value}</span>
    </div>
  )
}

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: '0 0 4px' },
  sub:          { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0, lineHeight: 1.5 },
  chips:        { display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 },
  chip:         { padding: '6px 12px', borderRadius: 20, border: '1px solid #DDD8CE', background: '#fff', cursor: 'pointer', fontSize: 12, fontFamily: 'system-ui', color: '#8A8680' },
  chipActive:   { background: 'var(--accent, var(--accent))', color: '#fff', border: '1px solid var(--accent, var(--accent))' },
  captureBtn:   { display: 'block', textAlign: 'center', padding: '32px 16px', border: '2px dashed var(--accent, var(--accent))', borderRadius: 12, cursor: 'pointer', fontSize: 16, fontFamily: 'system-ui', fontWeight: 600, color: 'var(--accent, var(--accent))', background: 'rgba(44,85,69,0.04)' },
  hint:         { textAlign: 'center', fontSize: 12, color: '#8A8680', fontFamily: 'system-ui', margin: '8px 0 0' },
  previewWrap:  { position: 'relative', marginBottom: 16 },
  previewImg:   { width: '100%', maxHeight: 280, objectFit: 'contain', borderRadius: 8, border: '1px solid #DDD8CE', background: '#fff' },
  scanningOverlay: { position: 'absolute', inset: 0, background: 'rgba(247,244,238,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'system-ui', fontSize: 15, fontWeight: 600, color: 'var(--accent, var(--accent))', borderRadius: 8 },
  retakeBtn:    { position: 'absolute', top: 8, right: 8, padding: '4px 10px', background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, cursor: 'pointer' },
  errorBox:     { background: '#FEF2F2', border: '1px solid #FECACA', color: '#991B1B', borderRadius: 6, padding: '10px 14px', fontSize: 13, fontFamily: 'system-ui', marginBottom: 12 },
  resultCard:   { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 10, padding: 16 },
  resultHeader: { marginBottom: 12 },
  detectedBadge:{ fontFamily: 'system-ui', fontSize: 11, color: 'var(--accent, var(--accent))', background: 'rgba(44,85,69,0.1)', padding: '4px 10px', borderRadius: 12, fontWeight: 600 },
  fieldGrid:    { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 12 },
  field:        { display: 'flex', flexDirection: 'column', gap: 2 },
  fieldLabel:   { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  fieldValue:   { fontFamily: 'system-ui', fontSize: 14, color: '#1E1E1E', fontWeight: 500 },
  lineItems:    { borderTop: '1px solid #EDE9DF', paddingTop: 10, marginBottom: 12 },
  lineHeader:   { fontFamily: 'system-ui', fontSize: 12, fontWeight: 600, color: '#1E1E1E', margin: '0 0 6px' },
  lineRow:      { display: 'flex', justifyContent: 'space-between', fontFamily: 'system-ui', fontSize: 12, color: '#444', padding: '3px 0' },
  lineQty:      { color: '#8A8680' },
  actions:      { display: 'flex', flexDirection: 'column', gap: 8 },
  btnPrimary:   { padding: '12px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  btnSecondary: { padding: '12px', background: 'transparent', color: 'var(--accent, var(--accent))', border: '1px solid var(--accent, var(--accent))', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  savedCard:    { background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.3)', borderRadius: 10, padding: 20, textAlign: 'center' },
  savedText:    { fontFamily: 'system-ui', fontSize: 15, fontWeight: 600, color: '#16a34a', margin: '0 0 12px' },
}
