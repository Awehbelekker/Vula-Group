/**
 * StockV2.jsx — the redesigned Stock tab (2026-10-20, UX redesign Phase F). Same three modes and
 * the same calls as VulaStock (stock/useStock.js): Stock-take (scan a count, review counted vs
 * system with the difference at cost, owner/manager applies), Receive (book a delivery, against a
 * purchase order or not) and Quick adjust (set / add / remove with the item's history). Barcodes
 * come from the phone's camera, a USB scanner or typing; a code nothing carries is linked to a
 * product on the spot. Amounts are the server's integer cents through formatMoney.
 */
import { useState } from 'react'
import { ArrowLeft, Ban, Camera, CameraOff, CheckCircle2, ClipboardList, Eye, PackageCheck, PackagePlus, Pencil, Play, Plus, Search, Minus, Equal } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { SA_TZ } from '../../lib/dates'
import { Button, Card, DataTable, EmptyState, Field, Input, PillTabs, Select, StatusChip } from '../ui/index.jsx'
import { itemKey, itemName, REASON, useBarcodeCamera, useCountSession, useProductSearch, useQuickAdjust, useReceive,
  useStockProducts, useStockTakes } from './useStock'
import './stock.css'

const MODES = [
  { id: 'count', Icon: ClipboardList, label: 'Stock-take' },
  { id: 'receive', Icon: PackagePlus, label: 'Receive' },
  { id: 'adjust', Icon: Pencil, label: 'Quick adjust' },
]
const money = (c) => formatMoney(c, { cents: 'always' })
const when = (iso, opts = { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('en-ZA', { ...opts, timeZone: SA_TZ })
}
const STATUS_TONE = { open: 'accent', applied: 'ok', cancelled: 'muted' }
const signed = (n) => `${n > 0 ? '+' : ''}${n}`

export default function StockV2({ tenantId, products: initial = [] }) {
  const [mode, setMode] = useState('count')
  const products = useStockProducts(tenantId, initial, mode)
  if (!tenantId) return <EmptyState Icon={PackagePlus} title="Choose a business first" />
  return (
    <section className="vst" aria-label="Stock">
      <PillTabs label="Stock" tabs={MODES} active={mode} onChange={setMode} />
      {mode === 'count' && <StockTake tenantId={tenantId} products={products} />}
      {mode === 'receive' && <Receive tenantId={tenantId} products={products} />}
      {mode === 'adjust' && <QuickAdjust tenantId={tenantId} products={products} />}
    </section>
  )
}

// ── shared pieces ─────────────────────────────────────────────────────────────

/** Camera (BarcodeDetector / zxing), a USB scanner or typing — each read is one onCode. */
function Scanner({ onCode, paused }) {
  const c = useBarcodeCamera({ onCode, paused })
  return (
    <Card className="vst-card">
      <div className="vst-video" hidden={!c.on}>
        <video ref={c.videoRef} muted playsInline aria-label="Camera view" />
        <span className="vst-aim" aria-hidden="true" />
      </div>
      <div className="vst-row">
        <Button variant={c.on ? 'secondary' : 'primary'} Icon={c.on ? CameraOff : Camera} onClick={() => c.setOn(v => !v)}>
          {c.on ? 'Stop camera' : 'Start camera'}
        </Button>
        <form className="vst-manual" onSubmit={c.submitManual}>
          <Input value={c.manual} onChange={e => c.setManual(e.target.value)} inputMode="numeric"
            aria-label="Barcode" placeholder="Type a barcode" />
          <Button variant="secondary" Icon={Plus} type="submit">Add</Button>
        </form>
      </div>
      {c.err && <p className="vst-error" role="alert">{c.err}</p>}
    </Card>
  )
}

/** Find a product by name, SKU or barcode; each pick is one onPick. */
function Picker({ products, onPick, label, hint }) {
  const { q, setQ, hits } = useProductSearch(products)
  return (
    <div className="vst-picker">
      <Field label={label} hint={hint}>
        <span className="vst-search">
          <Search size={16} aria-hidden="true" />
          <Input type="search" value={q} onChange={e => setQ(e.target.value)} placeholder="Name, SKU or barcode" />
        </span>
      </Field>
      {hits.length > 0 && (
        <ul className="vst-hits" aria-label="Matching products">
          {hits.map(p => (
            <li key={p.id}>
              <button type="button" onClick={() => { onPick(p); setQ('') }}>
                <span className="vst-grow">{p.name}</span>
                <span className="vst-muted">stock {p.stock_quantity ?? 'not tracked'}</span>
              </button>
            </li>))}
        </ul>)}
    </div>
  )
}

/** A scanned code no product carries: pick the product it belongs to, or skip it. */
function Linker({ resolver, products }) {
  const u = resolver.unknown
  if (!u) return null
  return (
    <Card className="vst-card vst-linker" role="group" aria-label="Link a new barcode">
      <h3 className="vst-h">Barcode <span className="vst-mono">{u.code}</span> isn't on any product yet</h3>
      <p className="vst-sub">Pick the product it belongs to — next time it scans straight in.</p>
      <Picker products={products} onPick={resolver.linkTo} label="Product" />
      <div><Button variant="secondary" size="sm" onClick={resolver.skip}>Skip</Button></div>
    </Card>
  )
}

/** Scanner + "find by name" + the unknown-barcode linker, the top of every mode. */
function Capture({ resolver, products, onCode, onPick, pickLabel, pickHint }) {
  return (
    <>
      <Linker resolver={resolver} products={products} />
      <Scanner onCode={onCode} paused={resolver.waiting} />
      <Card className="vst-card"><Picker products={products} onPick={onPick} label={pickLabel} hint={pickHint} /></Card>
    </>
  )
}

/** A list of items, each with a quantity box. */
function QtyList({ rows, label, onChange, extra }) {
  return (
    <Card pad={0} className="vst-card">
      <ul className="vst-lines" aria-label={label}>
        {rows.map(([k, l]) => (
          <li key={k}>
            <span className="vst-grow">{l.name}{extra?.(l)}</span>
            <Input type="number" min="0" step="any" inputMode="decimal" className="vst-qty" value={l.value}
              aria-label={`Quantity for ${l.name}`} onChange={e => onChange(k, l, e.target.value)} />
          </li>))}
      </ul>
    </Card>
  )
}

const Note = ({ text, tone }) => text ? <p className={tone === 'danger' ? 'vst-error' : 'vst-note'} role={tone === 'danger' ? 'alert' : 'status'}>{text}</p> : null

// ── Stock-take ────────────────────────────────────────────────────────────────

function StockTake({ tenantId, products }) {
  const t = useStockTakes(tenantId)
  if (t.countId) return <CountSession tenantId={tenantId} products={products} countId={t.countId} full={t.full}
    review={t.review} setReview={t.setReview} onClose={t.close} />
  return (
    <>
      <Card className="vst-card">
        <h3 className="vst-h">Start a stock-take</h3>
        <form className="vst-row" onSubmit={e => { e.preventDefault(); t.start() }}>
          <Field label="Name" style={{ flex: '1 1 220px', minWidth: 0 }}>
            <Input value={t.note} onChange={e => t.setNote(e.target.value)} placeholder="e.g. Month end — cold room" />
          </Field>
          <Button Icon={Play} type="submit" className="vst-end">Start</Button>
        </form>
        <p className="vst-tip">Several people can count into the same stock-take from their own phones.</p>
      </Card>
      <Note text={t.msg} tone="danger" />
      {t.counts.length === 0
        ? <EmptyState Icon={ClipboardList} title="No stock-takes yet">Start one above and scan your shelves.</EmptyState>
        : <DataTable caption="Stock-takes" rows={t.counts}
            columns={[
              { key: 'note', label: 'Stock-take', render: c => <b>{c.note || 'Stock-take'}</b> },
              { key: 'created_at', label: 'Started', render: c => `${when(c.created_at)}${c.started_by ? ` · ${c.started_by}` : ''}` },
              { key: 'status', label: 'Status', render: c => <StatusChip tone={STATUS_TONE[c.status] || 'muted'}>{c.status}</StatusChip> },
              { key: 'open', label: '', align: 'right', render: c => (
                <Button size="sm" variant="secondary" Icon={c.status === 'open' ? ClipboardList : Eye}
                  aria-label={`${c.status === 'open' ? 'Continue' : 'View'} ${c.note || 'stock-take'}`}
                  onClick={e => { e.stopPropagation(); t.setCountId(c.id) }}>{c.status === 'open' ? 'Continue' : 'View'}</Button>) },
            ]}
            stack={{
              title: c => c.note || 'Stock-take',
              detail: c => `${when(c.created_at)}${c.started_by ? ` · ${c.started_by}` : ''}`,
              trailing: c => <StatusChip tone={STATUS_TONE[c.status] || 'muted'}>{c.status}</StatusChip>,
            }}
            onRowClick={c => t.setCountId(c.id)} />}
      {t.open.length > 1 && <p className="vst-tip">{t.open.length} stock-takes are still open.</p>}
    </>
  )
}

function CountSession({ tenantId, products, countId, full, review, setReview, onClose }) {
  const s = useCountSession({ tenantId, products, countId, setReview, onClose })

  if (review) {
    const r = review
    const open = r.count.status === 'open'
    return (
      <>
        <div><Button variant="quiet" size="sm" Icon={ArrowLeft} onClick={() => setReview(null)}>Back to scanning</Button></div>
        <Card className="vst-card">
          <div className="vst-row">
            <h3 className="vst-h vst-grow">{r.count.note || 'Stock-take'}</h3>
            <StatusChip tone={STATUS_TONE[r.count.status] || 'muted'}>{r.count.status}</StatusChip>
          </div>
          <dl className="vst-facts">
            <div><dt>Items counted</dt><dd>{r.lines.length}</dd></div>
            <div><dt>Difference at cost</dt><dd className={`vula-money${r.variance_cents < 0 ? ' vst-neg' : ''}`}>{money(r.variance_cents)}</dd></div>
            {r.lines_without_cost > 0 && <div><dt>No cost price</dt><dd>{r.lines_without_cost} item{r.lines_without_cost === 1 ? '' : 's'}</dd></div>}
          </dl>
          {open && (full
            ? <div className="vst-actions">
                <Button Icon={CheckCircle2} disabled={s.busy || !r.lines.length} onClick={s.applyCount}>Apply to stock</Button>
                <Button variant="secondary" Icon={Ban} className="vst-danger" disabled={s.busy} onClick={s.cancelCount}>Cancel count</Button>
              </div>
            : <StatusChip tone="muted">The owner or a manager applies this count</StatusChip>)}
        </Card>
        <Note text={s.msg} />
        <DataTable caption="Counted vs system" rows={r.lines.map(l => ({ ...l, _key: itemKey(l.product_id, l.variant_id) }))} rowKey="_key"
          columns={[
            { key: 'name', label: 'Item' },
            { key: 'expected', label: 'System', align: 'right', render: l => l.expected ?? 'not tracked' },
            { key: 'counted', label: 'Counted', align: 'right', render: l => <b>{l.counted}</b> },
            { key: 'variance', label: 'Difference', align: 'right', render: l => <Diff n={l.variance} /> },
            { key: 'variance_cents', label: 'Value', align: 'right', render: l => <span className="vula-money">{money(l.variance_cents)}</span> },
          ]}
          stack={{
            title: l => l.name,
            detail: l => `System ${l.expected ?? 'not tracked'} · counted ${l.counted}`,
            trailing: l => <span className="vst-stack-trail"><Diff n={l.variance} /><span className="vula-money">{money(l.variance_cents)}</span></span>,
          }} />
        {open && r.uncounted.length > 0 && (
          <Card className="vst-card">
            <h3 className="vst-h">Not counted yet ({r.uncounted.length})</h3>
            <p className="vst-sub">These keep their current stock unless you count them:
              {' '}{r.uncounted.slice(0, 30).map(u => `${u.name} (${u.expected})`).join(', ')}{r.uncounted.length > 30 ? '…' : ''}</p>
          </Card>)}
      </>
    )
  }

  const waiting = s.queue.length
  return (
    <>
      <div className="vst-row">
        <Button variant="quiet" size="sm" Icon={ArrowLeft} onClick={onClose}>All stock-takes</Button>
        <span className="vst-push" role="status">
          <StatusChip tone={waiting ? 'warn' : 'ok'}>
            {waiting ? `${waiting} scan${waiting === 1 ? '' : 's'} waiting to send${navigator.onLine ? '…' : ' (offline)'}` : 'All scans saved'}
          </StatusChip>
        </span>
      </div>
      {s.status !== 'open'
        ? <Card className="vst-card"><div className="vst-row"><span className="vst-grow">This stock-take is {s.status}.</span>
            <Button size="sm" variant="secondary" Icon={Eye} onClick={s.openReview}>View</Button></div></Card>
        : <>
            <Capture resolver={s.resolver} products={products} onCode={s.onCode} onPick={p => s.record({ product: p, variant: null })}
              pickLabel="No barcode? Find it by name" pickHint="Each pick adds one." />
            {s.lines.length > 0 && <QtyList label="Counted so far" rows={s.lines.map(([k, l]) => [k, { ...l, value: l.counted }])}
              onChange={(k, l, v) => s.setCount(l, v)} />}
            <div className="vst-actions vst-actions--sticky">
              <Button Icon={ClipboardList} disabled={s.busy} onClick={s.openReview}>Review count</Button>
            </div>
            <Note text={s.msg} tone="danger" />
          </>}
    </>
  )
}

const Diff = ({ n }) => <span className={n < 0 ? 'vst-neg' : n > 0 ? 'vst-pos' : 'vst-muted'}>{signed(n)}</span>

// ── Receive a delivery ────────────────────────────────────────────────────────

function Receive({ tenantId, products }) {
  const r = useReceive(tenantId, products)
  return (
    <>
      <Card className="vst-card">
        <div className="vst-grid">
          <Field label="Purchase order">
            <Select value={r.po?.id || ''} onChange={e => r.choosePo(e.target.value)}>
              <option value="">No purchase order — just book what arrived</option>
              {r.pos.map(p => <option key={p.id} value={p.id}>{p.po_number || p.id.slice(0, 8)} · {p.supplier_name || 'supplier'} · {p.status}</option>)}
            </Select>
          </Field>
          <Field label="Delivery note / invoice number" hint="Optional">
            <Input value={r.ref} onChange={e => r.setRef(e.target.value)} />
          </Field>
        </div>
        <p className="vst-tip">Have the paper delivery note? Money › Scanner reads it and matches the lines for you.</p>
      </Card>
      <Capture resolver={r.resolver} products={products} onCode={r.onCode} onPick={p => r.add({ product: p, variant: null })} pickLabel="Add by name" />
      {r.rows.length > 0 && (
        <>
          {r.po && <div><Button variant="secondary" size="sm" Icon={PackageCheck} onClick={r.allAsOrdered}>Everything arrived as ordered</Button></div>}
          <QtyList label="Received" rows={r.rows.map(([k, l]) => [k, { ...l, value: l.qty }])} onChange={(k, l, v) => r.setQty(k, v)}
            extra={l => l.ordered != null && <span className="vst-muted"> · ordered {l.ordered}</span>} />
        </>)}
      <div className="vst-actions vst-actions--sticky">
        <Button Icon={PackagePlus} disabled={r.busy} onClick={r.book}>Book into stock</Button>
      </div>
      <Note text={r.msg} />
    </>
  )
}

// ── Quick adjust ──────────────────────────────────────────────────────────────

function QuickAdjust({ tenantId, products }) {
  const a = useQuickAdjust(tenantId, products)
  return (
    <>
      <Capture resolver={a.resolver} products={products} onCode={a.onCode} onPick={p => a.show({ product: p, variant: null })} pickLabel="…or find by name" />
      {a.hit && (
        <Card className="vst-card" role="group" aria-label={itemName(a.hit)}>
          <h3 className="vst-title">{itemName(a.hit)}</h3>
          <dl className="vst-facts"><div><dt>In stock</dt><dd className="vst-big">{a.qty ?? 'not tracked'}</dd></div></dl>
          <form className="vst-row" onSubmit={e => { e.preventDefault(); a.save('set') }}>
            <Field label="Quantity" style={{ width: 120 }}>
              <Input type="number" min="0" step="any" inputMode="decimal" value={a.val} onChange={e => a.setVal(e.target.value)} />
            </Field>
            <div className="vst-row vst-end">
              <Button size="sm" Icon={Equal} type="submit">Set to</Button>
              <Button size="sm" variant="secondary" Icon={Plus} type="button" onClick={() => a.save('add')}>Add</Button>
              <Button size="sm" variant="secondary" Icon={Minus} type="button" onClick={() => a.save('remove')}>Remove</Button>
            </div>
          </form>
          <Note text={a.msg} />
          {a.moves.length > 0 && (
            <>
              <h4 className="vst-h4">History</h4>
              <ul className="vst-moves" aria-label="Stock history">
                {a.moves.map(m => (
                  <li key={m.id}>
                    <span className="vst-grow">{REASON[m.reason] || m.reason} <Diff n={m.delta} />
                      {m.qty_after != null && <span className="vst-muted"> → {m.qty_after}</span>}</span>
                    <span className="vst-muted">{m.actor || ''} · {when(m.created_at, { day: 'numeric', month: 'short', year: 'numeric' })}</span>
                  </li>))}
              </ul>
            </>)}
        </Card>)}
      {!a.hit && <Note text={a.msg} tone="danger" />}
    </>
  )
}
