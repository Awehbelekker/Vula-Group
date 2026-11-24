/**
 * TillV2.jsx — Sell › Till (24 Nov 2026): sell over the counter. Tap or scan products into the
 * basket, take the money, hand over the goods. A hardware scanner types the barcode into the
 * search box and presses Enter; a phone or tablet can use its camera.
 *
 * Every amount charged is the server's (till/useTill.js): the basket total is a preview, the
 * receipt's total and change are what was recorded. Cash typed in is read as written.
 */
import { useMemo, useRef, useState } from 'react'
import { Banknote, Camera, Check, CreditCard, Minus, Plus, Printer, RefreshCw, ScanLine, ShoppingBasket, Trash2, X } from 'lucide-react'
import { formatMoney, randToCents } from '../../lib/money'
import { Button, Card, EmptyState, ErrorCard, Field, Input, PageHeader, Sheet, Skeleton, StatusChip, inputStyle, toast } from '../ui/index.jsx'
import { useBarcodeCamera } from '../stock/useStock'
import { lineCents, useTill } from './useTill'
import './till.css'

const money = (c) => formatMoney(c, { cents: 'always' })
const QUICK_CASH = [5000, 10000, 20000, 50000]          // notes a customer hands over, in cents

function Tile({ p, onPick }) {
  const out = !p.in_stock
  return (
    <li>
      <button type="button" className={`vtl-tile${out ? ' vtl-tile--out' : ''}`} onClick={() => onPick(p)}>
        {p.image ? <img className="vtl-img" src={p.image} alt="" loading="lazy" /> : <span className="vtl-img vtl-img--none" aria-hidden="true">{p.name.slice(0, 1).toUpperCase()}</span>}
        <span className="vtl-name">{p.name}</span>
        <span className="vtl-meta">
          <span className="vula-money vtl-price">{p.variants.length ? 'Options' : money(p.price_cents)}{p.by_kg && !p.variants.length ? ' /kg' : ''}</span>
          {out && <StatusChip tone="warn">Out of stock</StatusChip>}
        </span>
      </button>
    </li>
  )
}

function Line({ l, t }) {
  const step = l.by_kg ? 0.1 : 1
  const set = (q) => t.setQuantity(l.key, Math.max(0, Math.round(q * 1000) / 1000))
  return (
    <li className="vtl-line">
      <span className="vtl-line-main">
        <span className="vtl-line-name">{l.name}</span>
        <span className="vtl-sub">{money(l.price_cents)}{l.by_kg ? ' /kg' : ' each'}</span>
      </span>
      <span className="vtl-qty">
        <button type="button" className="vtl-qbtn" aria-label={`One less ${l.name}`} onClick={() => set(l.quantity - step)}><Minus size={16} /></button>
        {l.by_kg
          ? <input className="vtl-qin" inputMode="decimal" aria-label={`Kilograms of ${l.name}`} defaultValue={l.quantity} key={l.quantity}
              onBlur={(e) => { const n = Number(e.target.value.replace(',', '.')); if (Number.isFinite(n) && n > 0) set(n); else e.target.value = l.quantity }} />
          : <span className="vtl-qnum" aria-live="polite">{l.quantity}</span>}
        <button type="button" className="vtl-qbtn" aria-label={`One more ${l.name}`} onClick={() => set(l.quantity + step)}><Plus size={16} /></button>
      </span>
      <span className="vula-money vtl-line-amt">{money(lineCents(l))}</span>
      <button type="button" className="vtl-qbtn vtl-del" aria-label={`Remove ${l.name}`} onClick={() => t.remove(l.key)}><Trash2 size={16} /></button>
    </li>
  )
}

function PaySheet({ t, onClose }) {
  const [method, setMethod] = useState('cash')
  const [cash, setCash] = useState('')
  const tendered = randToCents(cash)
  const cashOk = method !== 'cash' || (tendered != null && tendered >= t.previewCents)
  const take = async () => {
    const sale = await t.charge(method, method === 'cash' ? { tendered_cents: tendered } : {})
    if (sale) onClose()
  }
  return (
    <Sheet open title={`Take payment — ${money(t.previewCents)}`} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Back</Button>
        <Button Icon={Check} onClick={take} disabled={t.busy || !cashOk}>{t.busy ? 'Recording…' : 'Payment taken'}</Button>
      </>}>
      <div className="vtl-form">
        <div className="vtl-methods" role="radiogroup" aria-label="How the customer paid">
          {t.methods.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={method === m.id} className="vtl-method" onClick={() => setMethod(m.id)}>
              {m.id === 'cash' ? <Banknote size={18} aria-hidden="true" /> : <CreditCard size={18} aria-hidden="true" />}{m.label}
            </button>))}
        </div>
        {method === 'cash' && (
          <>
            <Field label="Cash received" hint="The change is worked out when you record the sale.">
              <Input inputMode="decimal" autoFocus value={cash} onChange={(e) => setCash(e.target.value)} placeholder={money(t.previewCents)} />
            </Field>
            <div className="vtl-quick" aria-label="Quick amounts">
              <Button size="sm" variant="secondary" onClick={() => setCash((t.previewCents / 100).toFixed(2))}>Exact</Button>
              {QUICK_CASH.filter((c) => c >= t.previewCents).slice(0, 3).map((c) => (
                <Button key={c} size="sm" variant="secondary" onClick={() => setCash(String(c / 100))}>{money(c)}</Button>))}
            </div>
            {cash && tendered == null && <p className="vtl-error" role="alert">Enter an amount like 200 or 199.50.</p>}
            {tendered != null && tendered < t.previewCents && <p className="vtl-error" role="alert">That's less than the total.</p>}
          </>)}
        {method === 'card' && <p className="vtl-sub">Take the card on your own card machine first, then record it here. Vula doesn't charge the card.</p>}
        {t.error && <p className="vtl-error" role="alert">{t.error}</p>}
      </div>
    </Sheet>
  )
}

function Receipt({ r, onDone }) {
  return (
    <Sheet open title={`Sale ${r.number}`} onClose={onDone}
      footer={<>
        {r.receipt_url && <Button variant="secondary" Icon={Printer} onClick={() => window.open(r.receipt_url, '_blank', 'noopener')}>Receipt</Button>}
        <Button autoFocus onClick={onDone}>New sale</Button>
      </>}>
      <div className="vtl-form">
        <p className="vtl-paid"><Check size={20} aria-hidden="true" />Paid — {r.method_label}{r.repeat ? ' (already recorded)' : ''}</p>
        {r.change_cents != null && (
          <div className="vtl-change" role="status">
            <span>Change to give</span><span className="vula-money">{money(r.change_cents)}</span>
          </div>)}
        <ul className="vtl-rlines">
          {r.lines.map((l, i) => (
            <li key={i}><span>{l.quantity} × {l.name}</span><span className="vula-money">{money(l.total_cents)}</span></li>))}
          {r.discount_cents > 0 && <li><span>Discount</span><span className="vula-money">−{money(r.discount_cents)}</span></li>}
          <li className="vtl-rtotal"><span>Total</span><span className="vula-money">{money(r.total_cents)}</span></li>
          {r.tendered_cents != null && <li><span>Cash received</span><span className="vula-money">{money(r.tendered_cents)}</span></li>}
        </ul>
      </div>
    </Sheet>
  )
}

function Takings({ s, onRefresh }) {
  if (!s) return null
  return (
    <Card className="vtl-takings">
      <div className="vtl-takings-top">
        <h2 className="vtl-h">Today at the till</h2>
        <Button size="sm" variant="quiet" Icon={RefreshCw} onClick={onRefresh}>Update</Button>
      </div>
      <p className="vtl-takings-total"><span className="vula-money">{money(s.net_cents)}</span><span className="vtl-sub"> · {s.sales} sale{s.sales === 1 ? '' : 's'}</span></p>
      {s.by_method.length > 0 && (
        <ul className="vtl-rlines">
          {s.by_method.map((m) => <li key={m.method}><span>{m.label} · {m.count}</span><span className="vula-money">{money(m.cents)}</span></li>)}
          {s.refunded_cents > 0 && <li><span>Refunded</span><span className="vula-money">−{money(s.refunded_cents)}</span></li>}
        </ul>)}
      {s.sales === 0 && <p className="vtl-sub">No till sales yet today.</p>}
    </Card>
  )
}

export default function TillV2({ tenantId }) {
  const t = useTill(tenantId)
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('')
  const [choose, setChoose] = useState(null)
  const [paying, setPaying] = useState(false)
  const searchRef = useRef(null)

  const onCode = (code) => {
    const r = t.scan(code)
    if (r.result === 'choose') setChoose(r.product)
    else if (r.result === 'unknown') toast(`No product has the code ${code}. Add it to a product in Stock.`, 'danger')
    return r.result
  }
  const cam = useBarcodeCamera({ onCode, paused: paying || !!choose })

  const cats = useMemo(() => [...new Set((t.products || []).map((p) => p.category).filter(Boolean))].sort(), [t.products])
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (t.products || []).filter((p) => (!cat || p.category === cat)
      && (!s || `${p.name} ${p.sku || ''} ${p.barcode || ''}`.toLowerCase().includes(s)))
  }, [t.products, q, cat])

  const pick = (p) => (p.variants.length ? setChoose(p) : t.add(p))
  /** Enter in the search box: a scanner's barcode, else the only product left on screen. */
  const onSearchEnter = (e) => {
    if (e.key !== 'Enter' || !q.trim()) return
    e.preventDefault()
    const exact = t.scan(q.trim())
    if (exact.result === 'added') { setQ(''); return }
    if (exact.result === 'choose') { setChoose(exact.product); setQ(''); return }
    if (shown.length === 1) { pick(shown[0]); setQ('') } else if (shown.length === 0) toast('Nothing matches that.', 'danger')
  }

  if (t.products === null) return <Card><Skeleton lines={5} /></Card>
  if (t.loadError) return <ErrorCard what="the till" onRetry={t.load} />

  return (
    <div className="vtl">
      <PageHeader sub="Sell over the counter: tap or scan, take the money, hand it over. Stock and your books update with every sale." />
      <div className="vtl-cols">
        <section className="vtl-left" aria-label="Products">
          <div className="vtl-search">
            <input ref={searchRef} className="vula-input vtl-searchin" style={inputStyle} type="search" autoFocus value={q}
              onChange={(e) => setQ(e.target.value)} onKeyDown={onSearchEnter}
              placeholder="Search or scan a barcode" aria-label="Search products or scan a barcode" />
            <Button variant={cam.on ? 'primary' : 'secondary'} Icon={cam.on ? X : Camera} onClick={() => cam.setOn(!cam.on)}>{cam.on ? 'Stop camera' : 'Scan'}</Button>
          </div>
          {cam.err && <p className="vtl-error" role="alert">{cam.err}</p>}
          <div className="vtl-video" hidden={!cam.on}><video ref={cam.videoRef} muted playsInline /></div>
          {cats.length > 1 && (
            <div className="vtl-cats" role="group" aria-label="Categories">
              <button type="button" aria-pressed={!cat} onClick={() => setCat('')}>All</button>
              {cats.map((c) => <button key={c} type="button" aria-pressed={cat === c} onClick={() => setCat(cat === c ? '' : c)}>{c.replace(/_/g, ' ')}</button>)}
            </div>)}
          {shown.length === 0
            ? <EmptyState Icon={ScanLine} title={t.products.length ? 'Nothing matches' : 'No products yet'}>{t.products.length ? 'Try another word or scan the barcode.' : 'Add products under Sell › Products to sell them here.'}</EmptyState>
            : <ul className="vtl-grid" aria-label="Products to sell">{shown.map((p) => <Tile key={p.id} p={p} onPick={pick} />)}</ul>}
        </section>

        <aside className="vtl-right" aria-label="Basket">
          <Card className="vtl-basket">
            <div className="vtl-takings-top">
              <h2 className="vtl-h"><ShoppingBasket size={18} aria-hidden="true" /> Basket</h2>
              {t.lines.length > 0 && <Button size="sm" variant="quiet" Icon={Trash2} onClick={t.clear}>Clear</Button>}
            </div>
            {t.lines.length === 0
              ? <p className="vtl-sub">Tap a product or scan a barcode to start a sale.</p>
              : <ul className="vtl-lines">{t.lines.map((l) => <Line key={l.key} l={l} t={t} />)}</ul>}
            {t.error && !paying && <p className="vtl-error" role="alert">{t.error}</p>}
            <div className="vtl-total"><span>Total</span><span className="vula-money">{money(t.previewCents)}</span></div>
            <Button className="vtl-charge" disabled={t.lines.length === 0} onClick={() => { t.setError(''); setPaying(true) }}>
              Take payment{t.lines.length ? ` — ${money(t.previewCents)}` : ''}
            </Button>
          </Card>
          <Takings s={t.summary} onRefresh={t.loadSummary} />
        </aside>
      </div>

      {choose && (
        <Sheet open title={choose.name} onClose={() => setChoose(null)}>
          <ul className="vtl-options" aria-label={`Options for ${choose.name}`}>
            {choose.variants.map((v) => (
              <li key={v.id}><button type="button" className="vtl-option" disabled={!v.in_stock} onClick={() => { t.add(choose, v); setChoose(null) }}>
                <span>{v.label}{!v.in_stock ? ' — out of stock' : ''}</span><span className="vula-money">{money(v.price_cents)}</span>
              </button></li>))}
          </ul>
        </Sheet>)}
      {paying && <PaySheet t={t} onClose={() => setPaying(false)} />}
      {t.receipt && <Receipt r={t.receipt} onDone={() => { t.setReceipt(null); searchRef.current?.focus() }} />}
    </div>
  )
}
