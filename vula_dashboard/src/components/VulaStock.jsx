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
import { useState } from 'react'
import { ClipboardList, PackagePlus, Pencil } from 'lucide-react'
import { Card, Button, Badge, SectionTabs, EmptyState, inputStyle } from './ui/index.jsx'
import { T } from '../theme/tokens'
import { itemKey, itemName, REASON, useBarcodeCamera, useProductSearch, useStockTakes,
  useCountSession, useReceive, useQuickAdjust, useStockProducts } from './stock/useStock'
import StockV2 from './stock/StockV2'

export { toQty } from './stock/useStock'

const rands = (cents) => cents == null ? '—' : `${cents < 0 ? '−' : ''}R${(Math.abs(cents) / 100).toFixed(2)}`

// ── Camera barcode scanner ────────────────────────────────────────────────────

function BarcodeScanner({ onCode, paused }) {
  const { videoRef, on, setOn, err, manual, setManual, submitManual } = useBarcodeCamera({ onCode, paused })

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
              onSubmit={submitManual}>
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
  const { q, setQ, hits } = useProductSearch(products)
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

/** The resolver's "link this unknown barcode" card. */
function withLinker({ resolve, unknown, linkTo, skip, waiting }, products) {
  const linker = unknown && (
    <Card pad={14} style={{ marginBottom: 12, borderColor: T.warn }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Barcode {unknown.code} isn't on any product yet</div>
      <div style={{ fontSize: 12.5, color: T.muted, marginBottom: 8 }}>Pick the product it belongs to — next time it scans straight in.</div>
      <ProductPicker products={products} onPick={linkTo} />
      <Button size="sm" variant="ghost" style={{ marginTop: 8 }} onClick={skip}>Skip</Button>
    </Card>
  )
  return { resolve, linker, waiting }
}

// ── Stock-take ────────────────────────────────────────────────────────────────

function StockTake({ tenantId, products }) {
  const { full, counts, open, countId, setCountId, note, setNote, review, setReview, msg, start, close } = useStockTakes(tenantId)

  if (countId) return <CountSession tenantId={tenantId} products={products} countId={countId} full={full}
                                     review={review} setReview={setReview}
                                     onClose={close} />
  return (
    <div>
      <Card pad={14} style={{ marginBottom: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Start a stock-take</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="Name it, e.g. Month end — cold room" style={inputStyle} />
          <Button onClick={start}>Start</Button>
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
  const { queue, status, msg, busy, resolver, record, onCode, openReview, applyCount, cancelCount, setCount, lines } =
    useCountSession({ tenantId, products, countId, setReview, onClose })
  const { linker, waiting } = withLinker(resolver, products)

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
                  <Button variant="danger" size="sm" disabled={busy} onClick={cancelCount}>Cancel count</Button>
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
                <input type="number" min="0" step="any" value={l.counted} style={{ ...inputStyle, width: 80, padding: '5px 8px' }}
                       onChange={e => setCount(l, e.target.value)} />
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
  const { pos, po, ref, setRef, msg, busy, resolver, choosePo, add, onCode, book, allAsOrdered, setQty, rows } = useReceive(tenantId, products)
  const { linker, waiting } = withLinker(resolver, products)
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
            <Button size="sm" variant="ghost" onClick={allAsOrdered}>
              Everything arrived as ordered</Button></div>}
          {rows.map(([k, l]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${T.border}` }}>
              <div style={{ flex: 1, fontSize: 13.5 }}>{l.name}{l.ordered != null && <span style={{ color: T.muted }}> · ordered {l.ordered}</span>}</div>
              <input type="number" min="0" step="any" value={l.qty} style={{ ...inputStyle, width: 80, padding: '5px 8px' }}
                     onChange={e => setQty(k, e.target.value)} />
            </div>))}
        </Card>)}
      <Button disabled={busy} onClick={book}>Book into stock</Button>
      {msg && <div style={{ marginTop: 8, color: T.muted }}>{msg}</div>}
    </div>
  )
}

// ── Quick adjust ──────────────────────────────────────────────────────────────

function QuickAdjust({ tenantId, products }) {
  const { hit, qty, moves, val, setVal, msg, resolver, show, onCode, save } = useQuickAdjust(tenantId, products)
  const { linker, waiting } = withLinker(resolver, products)

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
  { id: 'count', icon: '📋', Icon: ClipboardList, label: 'Stock-take' },
  { id: 'receive', icon: '📦', Icon: PackagePlus, label: 'Receive' },
  { id: 'adjust', icon: '✏️', Icon: Pencil, label: 'Quick adjust' },
]

export default function VulaStock({ v2 = false, ...props }) {
  return v2 ? <StockV2 {...props} /> : <StockV1 {...props} />
}

function StockV1({ tenantId, products: initial = [] }) {
  const [mode, setMode] = useState('count')
  const products = useStockProducts(tenantId, initial, mode)
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
