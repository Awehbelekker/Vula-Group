/**
 * ScannerV2.jsx — the redesigned Smart Scanner (2026-10-19, UX redesign Phase F). Same flow as
 * VulaSmartScanner (scanner/useSmartScanner.js): pick the kind of document, take a photo (the
 * phone's own camera) or choose one, the server reads it, review what it found, then one tap books
 * it — commit to books, receive matched lines into stock, or update cost prices. Nothing is booked
 * before that tap. Amounts are the server's integer cents through formatMoney.
 */
import { Camera, CheckCircle2, FileText, PackageCheck, Receipt, RotateCcw, ScanLine, Sparkles, Tags } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, Field, Input, PillTabs, Select, StatusChip } from '../ui/index.jsx'
import { useSmartScanner } from './useSmartScanner'
import './scanner.css'

const TYPES = [
  { id: 'auto', label: 'Auto-detect', Icon: Sparkles, hint: 'Vula works out what kind of document it is.' },
  { id: 'receipt', label: 'Receipt', Icon: Receipt, hint: 'A supplier receipt becomes an expense.' },
  { id: 'delivery_note', label: 'Delivery note', Icon: PackageCheck, hint: 'Lines are matched to your products and received into stock.' },
  { id: 'invoice', label: 'Invoice', Icon: FileText, hint: 'A supplier invoice becomes a bill in your books.' },
  { id: 'price_list', label: 'Price list', Icon: Tags, hint: 'Proposes new cost prices for the products it finds.' },
]
const TYPE_NAME = { receipt: 'Receipt', delivery_note: 'Delivery note', invoice: 'Invoice', price_list: 'Price list', auto: 'Document' }
const money = (c) => formatMoney(c, { cents: 'always' })
const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+/, '')   // the shared messages start with an emoji

export default function ScannerV2({ tenantId, products = [], onExpenseCreated, onStockUpdated, inSheet = false }) {
  const x = useSmartScanner({ tenantId, onExpenseCreated, onStockUpdated })
  const r = x.result
  const detected = r?.doc_type || x.docType
  const type = TYPES.find(t => t.id === x.docType) || TYPES[0]
  const canCommit = r && detected !== 'price_list' && (['receipt', 'invoice', 'delivery_note'].includes(detected) || r.total_cents != null)
  const lines = x.lineMatches || []

  return (
    <section className={`vsc${inSheet ? ' vsc--sheet' : ''}`} aria-label="Smart Scanner">
      {!inSheet && <p className="vsc-sub">Photograph a receipt, delivery note, invoice or price list. Vula reads it, you check it, and one tap books it. Nothing is booked before that tap.</p>}

      {!x.saved && (
        <>
          <PillTabs label="Document type" tabs={TYPES} active={x.docType} onChange={x.setDocType} />
          {!x.preview && (
            <Card className="vsc-capture">
              <input ref={x.fileRef} id="vsc-file" className="vsc-file" type="file" accept="image/*" capture="environment" onChange={x.handleImage} />
              <span className="vsc-capture-ic" aria-hidden="true"><Camera size={28} strokeWidth={1.75} /></span>
              <label htmlFor="vsc-file" className="vsc-capture-btn"><ScanLine size={18} aria-hidden="true" />Take a photo or choose one</label>
              <p className="vsc-sub">{type.hint}</p>
              <p className="vsc-tip">Flat, well lit and the whole page in frame reads best.</p>
            </Card>
          )}
          {x.preview && (
            <Card pad={0} className="vsc-preview">
              <img src={x.preview} alt="The document you photographed" className="vsc-img" />
              {x.scanning && <p className="vsc-reading" role="status"><span className="vsc-spin" aria-hidden="true" />Reading the document…</p>}
              <div className="vsc-preview-bar"><Button size="sm" variant="secondary" Icon={RotateCcw} onClick={x.reset}>Retake</Button></div>
            </Card>
          )}
        </>
      )}

      {x.error && <p className="vsc-error" role="alert">{x.error}</p>}

      {r && !x.saved && (
        <Card className="vsc-card">
          <div className="vsc-row">
            <h3 className="vsc-h">What Vula read</h3>
            <StatusChip tone="accent">{TYPE_NAME[detected] || detected}</StatusChip>
            <StatusChip tone={r.confidence === 'high' ? 'ok' : r.confidence === 'low' ? 'warn' : 'muted'}>{r.confidence || 'medium'} confidence</StatusChip>
          </div>
          <dl className="vsc-facts">
            {r.supplier && <div><dt>Supplier</dt><dd>{r.supplier}</dd></div>}
            {r.customer && <div><dt>Customer</dt><dd>{r.customer}</dd></div>}
            {r.date && <div><dt>Date</dt><dd>{r.date}</dd></div>}
            {r.category && <div><dt>Category</dt><dd>{r.category}</dd></div>}
            {r.total_cents != null && <div className="vsc-total"><dt>Total</dt><dd className="vula-money">{money(r.total_cents)}</dd></div>}
          </dl>

          {r.line_items?.length > 0 && !lines.length && (
            <>
              <h4 className="vsc-h4">Lines ({r.line_items.length})</h4>
              <ul className="vsc-lines">
                {r.line_items.map((it, i) => (
                  <li key={i}><span className="vsc-grow">{it.description}</span>
                    <span className="vsc-muted">{it.quantity}{it.unit ? ` ${it.unit}` : ''}</span>
                    {it.total_cents != null && <span className="vula-money">{money(it.total_cents)}</span>}</li>))}
              </ul>
            </>)}

          {x.commitPreview && <Terms p={x.commitPreview} />}

          {lines.length > 0 && (
            <fieldset className="vsc-match">
              <legend className="vsc-h4">Match lines to your products ({lines.filter(l => l.match).length}/{lines.length} found)</legend>
              <ul className="vsc-lines vsc-lines--match">
                {lines.map((l, i) => (
                  <li key={i}>
                    <label className="vsc-check">
                      <input type="checkbox" checked={l.include} onChange={e => x.updateLine(i, { include: e.target.checked })} />
                      <span>{l.description}{l.unit_price_cents ? <span className="vsc-muted"> · {money(l.unit_price_cents)} each</span> : null}</span>
                    </label>
                    <div className="vsc-match-fields">
                      <Field label="Product">
                        <Select value={l.product_id} aria-label={`Product for ${l.description}`}
                          onChange={e => x.updateLine(i, { product_id: e.target.value, include: !!e.target.value })}>
                          <option value="">Not one of my products</option>
                          {l.match && <option value={l.match.product_id}>{l.match.name}</option>}
                          {(l.suggestions || []).map(p => <option key={p.product_id} value={p.product_id}>Did you mean: {p.name}</option>)}
                          {products.filter(p => !p.archived && p.id !== l.match?.product_id && !(l.suggestions || []).some(s => s.product_id === p.id))
                            .map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                        </Select>
                      </Field>
                      {detected !== 'price_list' && (
                        <Field label="Quantity">
                          <Input type="number" min="0" step="any" value={l.qty} aria-label={`Quantity for ${l.description}`}
                            onChange={e => x.updateLine(i, { qty: Math.max(0, Math.round((parseFloat(e.target.value) || 0) * 10000) / 10000) })} />
                        </Field>)}
                    </div>
                  </li>))}
              </ul>
            </fieldset>)}

          {x.stockMsg && <p className="vsc-ok" role="status">{plain(x.stockMsg)}</p>}

          <div className={`vsc-actions${inSheet ? '' : ' vsc-actions--sticky'}`}>
            {canCommit && (
              <Button Icon={CheckCircle2} disabled={x.saving} onClick={x.commitToBooks}>
                {x.saving ? 'Saving to books…' : `Commit to books${r.total_cents ? ` · ${money(r.total_cents)}` : ''}`}
              </Button>)}
            {lines.length > 0 && detected !== 'price_list' && (
              <Button variant="secondary" Icon={PackageCheck} disabled={x.saving} onClick={x.bookStock}>{x.saving ? 'Booking…' : 'Book ticked lines into stock'}</Button>)}
            {lines.some(l => l.unit_price_cents > 0) && (
              <Button variant="secondary" Icon={Tags} disabled={x.saving} onClick={x.updateCosts}>{x.saving ? 'Updating…' : 'Update cost prices'}</Button>)}
          </div>
        </Card>
      )}

      {x.saved && (
        <Card className="vsc-card vsc-done" role="status">
          <span className="vsc-done-ic" aria-hidden="true"><CheckCircle2 size={28} strokeWidth={1.75} /></span>
          <p className="vsc-h">{x.committed?.message || 'Saved'}</p>
          {x.committed && <p className="vsc-sub">Saved as {x.committed.record_type}{x.committed.kb_chunks_added > 0 ? ` · ${x.committed.kb_chunks_added} knowledge chunks added` : ''}</p>}
          <Button Icon={ScanLine} onClick={x.reset}>Scan another</Button>
        </Card>
      )}
    </section>
  )
}

/** Payment terms and the due date the server worked out (scan/commit preview), in words. */
function Terms({ p }) {
  const d = p.days_until_due
  const [tone, head] = d < 0 ? ['danger', 'Overdue'] : d === 0 ? ['danger', 'Due today'] : d != null && d <= 7 ? ['warn', 'Due soon'] : ['ok', 'Payment terms']
  return (
    <div className={`vsc-terms vsc-terms--${tone}`}>
      <p className="vsc-row"><StatusChip tone={tone}>{head}</StatusChip>
        {p.due_date && <b>Due {p.due_date}{d != null ? ` (${Math.abs(d)} day${Math.abs(d) === 1 ? '' : 's'}${d < 0 ? ' overdue' : ''})` : ''}</b>}</p>
      <p className="vsc-body">{p.supplier_known ? `Supplier "${p.supplier}" — ${p.payment_terms_days} day terms` : `New supplier — defaulting to ${p.payment_terms_days} day terms`}</p>
      {p.supplier_match && (
        <p className="vsc-muted">Matched <b>{p.supplier_match.supplier_name}</b> via {p.supplier_match.tier} ({Math.round((p.supplier_match.confidence || 0) * 100)}%)
          {p.supplier_match.auto_applied ? ' — terms applied automatically' : ' — confirm to apply'}</p>)}
    </div>
  )
}
