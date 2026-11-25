/**
 * OrderCourier.jsx — "Courier" in the order drawer (23 Nov 2026): book a courier for this order,
 * see where the parcel is, print the waybill, cancel. Shown only when a courier is connected
 * (Delivery › Couriers) or the order already has a booking.
 *
 * Two steps, always: "Get prices" books nothing; the Book button names the exact price, and the
 * server refuses if it moved in between. The customer's address is free text on the order, so the
 * server's split of it is shown here to correct before anything is priced.
 */
import { useState } from 'react'
import { MapPin, Package, Printer, RefreshCw, Search, Truck, X } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Field, Input, Sheet, StatusChip, confirmDialog, toast } from '../ui/index.jsx'
import { AddressFields, ParcelFields } from './CourierSetup'
import { EMPTY_ADDRESS, useOrderShipping } from './useCouriers'
import './couriers.css'

const money = (c) => formatMoney(c, { cents: 'always' })
const TONE = { Delivered: 'ok', Cancelled: 'muted', 'Not booked': 'danger', 'Booking…': 'warn' }
const when = (ts) => (ts ? String(ts).slice(0, 16).replace('T', ' ') : '')

function Booking({ s, c }) {
  const busy = (what) => c.busy === `${what}:${s.id}`
  const cancel = async () => {
    const lost = !s.tracking_reference
    const ok = await confirmDialog(lost
      ? 'Clear this booking? Only do this once you have checked on the courier’s own site that it was not made.'
      : `Cancel waybill ${s.tracking_reference} with ${s.courier}?`, { danger: true, confirmLabel: lost ? 'Clear it' : 'Cancel the courier' })
    if (ok && await c.cancel(s.id)) toast(lost ? 'Cleared.' : 'Courier cancelled.')
  }
  const last = (s.events || [])[s.events?.length - 1]
  return (
    <div className="vcr-booking">
      <div className="vcr-booking-top">
        <Truck size={16} aria-hidden="true" />
        <strong>{s.courier}</strong>
        <StatusChip tone={TONE[s.status_text] || 'info'}>{s.status_text}</StatusChip>
        {s.mode === 'test' && <StatusChip tone="warn">Test</StatusChip>}
      </div>
      {s.tracking_reference && <p className="vcr-line">Waybill <span className="vcr-code">{s.tracking_reference}</span> · {s.service_name || s.service_code} · {money(s.quoted_cents)}</p>}
      {s.pickup_point_name && <p className="vcr-line"><MapPin size={14} aria-hidden="true" /> To {s.pickup_point_name}</p>}
      {last && <p className="vcr-sub">Last update {when(last.at)}{last.location ? ` · ${last.location}` : ''}</p>}
      {s.error && <p className="vsl-error" role="alert">{s.error}</p>}
      {s.live && s.tracking_reference && !s.verified_at && (
        <p className="vcr-sub">Booked, but the courier hasn't confirmed it back to Vula yet — press Update.</p>)}
      {s.live && (
        <div className="vcr-actions">
          {s.tracking_reference && <Button size="sm" variant="secondary" Icon={Printer} disabled={busy('label')} onClick={() => c.openLabel(s.id)}>Waybill</Button>}
          {s.tracking_reference && <Button size="sm" variant="secondary" Icon={RefreshCw} disabled={busy('refresh')} onClick={() => c.refresh(s.id)}>{busy('refresh') ? 'Checking…' : 'Update'}</Button>}
          {(s.can_cancel || !s.tracking_reference) && <Button size="sm" variant="quiet" Icon={X} disabled={busy('cancel')} onClick={cancel}>{s.tracking_reference ? 'Cancel' : 'Clear'}</Button>}
        </div>)}
    </div>
  )
}

function BookSheet({ c, order, tenantId, onClose }) {
  const [to, setTo] = useState('door')
  const [address, setAddress] = useState(null)          // null = let the server split the order's own text
  const [parcel, setParcel] = useState(null)
  const [near, setNear] = useState('')
  const [points, setPoints] = useState(null)
  const [point, setPoint] = useState(null)
  const [picked, setPicked] = useState(null)
  const q = c.quote

  const opts = () => ({
    ...(to === 'point' && point ? { pickup_point: { id: point.id, name: point.name } } : {}),
    ...(to === 'door' && address ? { address } : {}),
    ...(parcel ? { parcel } : {}),
  })
  const prices = async () => { setPicked(null); await c.getPrices(opts()) }
  const search = async () => { setPoint(null); setPoints(await c.findPoints(near || order.delivery_address || '')) }
  const book = async () => {
    const rate = (q?.rates || []).find((r) => r.code === picked)
    if (!rate) return
    const s = await c.book(rate, opts())
    if (s) { toast(`Courier booked — waybill ${s.tracking_reference}.`); onClose() } else setPicked(null)
  }
  const shownAddress = address || q?.address || EMPTY_ADDRESS
  const shownParcel = parcel || q?.parcel || { length_cm: '', width_cm: '', height_cm: '', weight_kg: '' }
  const rate = (q?.rates || []).find((r) => r.code === picked)

  return (
    <Sheet open title={`Book a courier — ${order.display_id}`} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Close</Button>
        {rate
          ? <Button onClick={book} disabled={c.busy === 'book'}>{c.busy === 'book' ? 'Booking…' : `Book for ${money(rate.cents)}`}</Button>
          : <Button onClick={prices} disabled={c.busy === 'quote' || (to === 'point' && !point)}>{c.busy === 'quote' ? 'Getting prices…' : 'Get prices'}</Button>}
      </>}>
      <div className="vcr-form">
        <div className="vcr-toggle" role="group" aria-label="Deliver to">
          <button type="button" aria-pressed={to === 'door'} onClick={() => { setTo('door'); c.setQuote(null); setPicked(null) }}>To the door</button>
          <button type="button" aria-pressed={to === 'point'} onClick={() => { setTo('point'); c.setQuote(null); setPicked(null) }}>To a locker or counter</button>
        </div>

        {to === 'door' && (
          <>
            <p className="vcr-sub">The customer wrote: {order.delivery_address || 'no address'}</p>
            {(q || address) && <AddressFields value={shownAddress} company={false} tenantId={tenantId} onChange={(a) => { setAddress(a); setPicked(null); c.setQuote(null) }} />}
          </>)}

        {to === 'point' && (
          <>
            <div className="vcr-search">
              <Field label="Near (suburb, town or address)"><Input value={near} onChange={(e) => setNear(e.target.value)} placeholder={order.delivery_address || 'Suburb and town'} /></Field>
              <Button variant="secondary" Icon={Search} onClick={search} disabled={c.busy === 'points'}>{c.busy === 'points' ? 'Looking…' : 'Find'}</Button>
            </div>
            {points && points.length === 0 && <p className="vcr-sub">None found near there.</p>}
            {(points || []).length > 0 && (
              <ul className="vcr-options" aria-label="Lockers and counters">
                {points.map((p) => (
                  <li key={p.id}><label className="vcr-option">
                    <input type="radio" name="vcr-point" checked={point?.id === p.id} onChange={() => { setPoint(p); setPicked(null); c.setQuote(null) }} />
                    <span className="vcr-option-main"><strong>{p.name}</strong><span className="vcr-sub">{p.address}</span></span>
                    {p.km != null && <span className="vcr-sub">{p.km < 10 ? p.km.toFixed(1) : Math.round(p.km)} km</span>}
                  </label></li>))}
              </ul>)}
          </>)}

        {(q || parcel) && (
          <>
            <h3 className="vcr-h"><Package size={16} aria-hidden="true" /> Parcel</h3>
            {q?.weight_from === 'products' && !parcel && <p className="vcr-sub">Weight is from the products on the order; the box is your usual one.</p>}
            {q?.weight_from === 'usual' && !parcel && <p className="vcr-sub">This is your usual parcel — change it if this order is bigger.</p>}
            <ParcelFields value={shownParcel} onChange={(p) => { setParcel(p); setPicked(null); c.setQuote(null) }} />
          </>)}

        {q?.missing?.length > 0 && (
          <div className="vsl-error" role="alert">Still needed before a price can be given:
            <ul className="vcr-missing">{q.missing.map((m) => <li key={m}>{m}</li>)}</ul></div>)}
        {c.error && <p className="vsl-error" role="alert">{c.error}</p>}

        {(q?.rates || []).length > 0 && (
          <>
            <h3 className="vcr-h">{q.courier}{q.mode === 'test' ? ' (test)' : ''} — choose a service</h3>
            <ul className="vcr-options" aria-label="Courier services">
              {q.rates.map((r) => (
                <li key={r.code}><label className="vcr-option">
                  <input type="radio" name="vcr-rate" checked={picked === r.code} onChange={() => setPicked(r.code)} />
                  <span className="vcr-option-main"><strong>{r.name}</strong>
                    <span className="vcr-sub">{[r.box, r.delivery_from && `Delivery from ${r.delivery_from}`].filter(Boolean).join(' · ')}</span></span>
                  <span className="vula-money vcr-price">{money(r.cents)}</span>
                </label></li>))}
            </ul>
            <p className="vcr-sub">Prices include VAT and are charged to your courier account when you book.</p>
          </>)}
      </div>
    </Sheet>
  )
}

export default function OrderCourier({ tenantId, order }) {
  const c = useOrderShipping(tenantId, order.id)
  const [open, setOpen] = useState(false)
  if (!c.loaded || (!c.connected && c.shipments.length === 0)) return null
  const live = c.shipments.find((s) => s.live)
  const sendable = !['cancelled', 'refunded'].includes(order.status)
  return (
    <section className="vcr-panel" aria-label="Courier">
      <h3 className="vcr-panel-h">Courier</h3>
      {c.shipments.map((s) => <Booking key={s.id} s={s} c={c} />)}
      {c.error && !open && <p className="vsl-error" role="alert">{c.error}</p>}
      {!live && c.connected && sendable && (
        <Button variant="secondary" Icon={Truck} onClick={() => { c.setError(''); c.setQuote(null); setOpen(true) }}>Book a courier</Button>)}
      {open && <BookSheet c={c} order={order} tenantId={tenantId} onClose={() => setOpen(false)} />}
    </section>
  )
}
