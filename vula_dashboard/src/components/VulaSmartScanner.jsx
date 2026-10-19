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
 * Data and actions: scanner/useSmartScanner.js (shared with the redesign's ScannerV2, shown when
 * the caller passes v2).
 */

import { useSmartScanner } from './scanner/useSmartScanner'
import ScannerV2 from './scanner/ScannerV2'

const DOC_TYPES = [
  { id: 'auto',          label: '✨ Auto-detect',     hint: 'Let AI figure it out' },
  { id: 'receipt',       label: '🧾 Supplier receipt', hint: '→ creates an expense' },
  { id: 'delivery_note', label: '📦 Delivery note',    hint: '→ updates stock' },
  { id: 'invoice',       label: '📄 Invoice',          hint: '→ pre-fills invoice' },
  { id: 'price_list',    label: '💲 Price list',       hint: '→ updates cost prices' },
]

export default function VulaSmartScanner(props) {
  if (props.v2) return <ScannerV2 {...props} />
  return <SmartScannerV1 {...props} />
}

function SmartScannerV1({ tenantId, products = [], onExpenseCreated, onStockUpdated }) {
  const { docType, setDocType, preview, scanning, result, commitPreview, committed, error, saving, saved, lineMatches, stockMsg, fileRef, updateLine, handleImage, commitToBooks, bookStock, updateCosts, reset } =
    useSmartScanner({ tenantId, onExpenseCreated, onStockUpdated })

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
              background: commitPreview.days_until_due < 0 ? 'var(--danger-soft)'
                        : commitPreview.days_until_due <= 7 ? '#FFFBEB' : 'var(--ok-soft)',
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
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '20px 1fr 64px', gap: 6, alignItems: 'center', padding: '4px 0', fontSize: 12}}>
                    <input type="checkbox" checked={l.include} onChange={e => updateLine(i, { include: e.target.checked })} />
                    <div>
                      <div style={{ color: 'var(--muted)' }}>{l.description}{l.unit_price_cents ? ` · ${fmt(l.unit_price_cents)} each` : ''}</div>
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
                      <input type="number" min="0" step="any" value={l.qty} onChange={e => updateLine(i, { qty: Math.max(0, Math.round((parseFloat(e.target.value) || 0) * 10000) / 10000) })}
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
            {stockMsg && <div style={{ fontSize: 13, color: 'var(--ok)' }}>{stockMsg}</div>}
          </div>
        </div>
      )}

      {/* Saved confirmation */}
      {saved && committed && (
        <div style={s.savedCard}>
          <p style={{ ...s.savedText, fontSize: 14 }}>{committed.message}</p>
          <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
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
  h3:           { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:          { fontSize: 13, color: 'var(--muted)', margin: 0, lineHeight: 1.5 },
  chips:        { display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 },
  chip:         { padding: '6px 12px', borderRadius: 20, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer', fontSize: 12, color: 'var(--muted)' },
  chipActive:   { background: 'var(--accent, var(--accent))', color: '#fff', border: '1px solid var(--accent, var(--accent))' },
  captureBtn:   { display: 'block', textAlign: 'center', padding: '32px 16px', border: '2px dashed var(--accent, var(--accent))', borderRadius: 12, cursor: 'pointer', fontSize: 16, fontWeight: 600, color: 'var(--accent, var(--accent))', background: 'rgba(44,85,69,0.04)' },
  hint:         { textAlign: 'center', fontSize: 12, color: 'var(--muted)', margin: '8px 0 0' },
  previewWrap:  { position: 'relative', marginBottom: 16 },
  previewImg:   { width: '100%', maxHeight: 280, objectFit: 'contain', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)' },
  scanningOverlay: { position: 'absolute', inset: 0, background: 'rgba(247,244,238,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 600, color: 'var(--accent, var(--accent))', borderRadius: 8 },
  retakeBtn:    { position: 'absolute', top: 8, right: 8, padding: '4px 10px', background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, cursor: 'pointer' },
  errorBox:     { background: 'var(--danger-soft)', border: '1px solid var(--danger-soft)', color: 'var(--danger)', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 12 },
  resultCard:   { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 },
  resultHeader: { marginBottom: 12 },
  detectedBadge:{ fontSize: 11, color: 'var(--accent, var(--accent))', background: 'rgba(44,85,69,0.1)', padding: '4px 10px', borderRadius: 12, fontWeight: 600 },
  fieldGrid:    { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 12 },
  field:        { display: 'flex', flexDirection: 'column', gap: 2 },
  fieldLabel:   { fontSize: 11, color: 'var(--muted)' },
  fieldValue:   { fontSize: 14, color: 'var(--ink)', fontWeight: 500 },
  lineItems:    { borderTop: '1px solid var(--border-soft)', paddingTop: 10, marginBottom: 12 },
  lineHeader:   { fontSize: 12, fontWeight: 600, color: 'var(--ink)', margin: '0 0 6px' },
  lineRow:      { display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text)', padding: '3px 0' },
  lineQty:      { color: 'var(--muted)' },
  actions:      { display: 'flex', flexDirection: 'column', gap: 8 },
  btnPrimary:   { padding: '12px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
  btnSecondary: { padding: '12px', background: 'transparent', color: 'var(--accent, var(--accent))', border: '1px solid var(--accent, var(--accent))', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
  savedCard:    { background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.3)', borderRadius: 10, padding: 20, textAlign: 'center' },
  savedText:    { fontSize: 15, fontWeight: 600, color: 'var(--ok)', margin: '0 0 12px' },
}
