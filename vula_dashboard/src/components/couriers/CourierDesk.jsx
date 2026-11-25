/**
 * CourierDesk.jsx — on Delivery › Couriers (25 Nov 2026): what the business owes or has left with
 * its courier, and sending a parcel that belongs to no order (a sample, a return, documents).
 * Loaded only when a courier is connected.
 *
 * The balance is read from the courier each time and shown as the courier gives it; nothing is
 * worked out here. Money is added at the courier itself, so there is a link to its own portal.
 * A parcel is booked in the same two steps as an order's courier (prices, then the chosen price).
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, ExternalLink, PackagePlus, RefreshCw, Wallet } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, StatusChip } from '../ui/index.jsx'
import { BookSheet, Booking } from './OrderCourier'
import { courierAccount, useParcels } from './useCouriers'
import './couriers.css'

const money = (c) => formatMoney(c, { cents: 'always' })

function Account({ tenantId }) {
  const [a, setA] = useState(undefined)             // undefined = asking, null = couldn't be read
  const [busy, setBusy] = useState(false)
  const load = async () => { setBusy(true); setA(await courierAccount(tenantId)); setBusy(false) }
  useEffect(() => { load() }, [tenantId])            // eslint-disable-line react-hooks/exhaustive-deps
  if (a === undefined || !a?.known) return null     // a courier that doesn't say, or a team member who may not see it
  const prepaid = a.kind === 'prepaid'
  return (
    <section className="vcr-account" aria-label="Your courier account">
      <div className="vcr-account-top">
        <Wallet size={16} aria-hidden="true" />
        <strong>Your account with {a.courier}</strong>
        {a.mode === 'test' && <StatusChip tone="warn">Test</StatusChip>}
        {a.on_hold && <StatusChip tone="danger">On hold</StatusChip>}
        <Button size="sm" variant="quiet" Icon={RefreshCw} onClick={load} disabled={busy}>{busy ? 'Checking…' : 'Check again'}</Button>
      </div>
      <dl className="vcr-figures">
        {prepaid
          ? <div><dt>Credit left</dt><dd className="vula-money">{money(a.available_cents)}</dd></div>
          : <div><dt>Owed to the courier</dt><dd className="vula-money">{money(a.owed_cents)}</dd></div>}
        {!prepaid && a.available_cents != null && <div><dt>Left before the limit</dt><dd className="vula-money">{money(a.available_cents)}</dd></div>}
        {!prepaid && a.credit_limit_cents != null && <div><dt>Credit limit</dt><dd className="vula-money">{money(a.credit_limit_cents)}</dd></div>}
      </dl>
      {a.on_hold && <p className="vsl-error" role="alert"><AlertTriangle size={14} aria-hidden="true" /> The courier has put this account on hold, so bookings will be refused until it is settled with them.</p>}
      {!a.on_hold && a.low && <p className="vsl-error" role="alert"><AlertTriangle size={14} aria-hidden="true" /> {prepaid ? 'Less than R200 of credit is left. Top up before the next booking.' : 'Less than R200 is left before the credit limit. Pay the courier before the next booking.'}</p>}
      <p className="vcr-sub">
        {prepaid ? 'Each booking comes off this credit. ' : `The courier invoices you${a.payment_terms_days ? `, payable in ${a.payment_terms_days} days` : ''}. `}
        Money is paid to the courier directly, not through Vula.
        {a.mode === 'test' && ' This is a test account: no real money moves.'}
      </p>
      {a.portal_url && <a className="vcr-link" href={a.portal_url} target="_blank" rel="noopener noreferrer">{prepaid ? 'Top up' : 'Pay'} at {a.courier} <ExternalLink size={14} aria-hidden="true" /></a>}
    </section>
  )
}

export default function CourierDesk({ tenantId }) {
  const c = useParcels(tenantId)
  const [open, setOpen] = useState(false)
  return (
    <div className="vsl-divide vcr-form">
      <Account tenantId={tenantId} />
      <div className="vcr-save">
        <Button Icon={PackagePlus} onClick={() => { c.setError(''); c.setQuote(null); setOpen(true) }}>Send a parcel</Button>
      </div>
      <p className="vcr-sub">For something that isn’t an order: a sample, a return, documents. For an order, open the order and choose Courier.</p>
      {c.error && !open && <p className="vsl-error" role="alert">{c.error}</p>}
      {c.shipments.length > 0 && (
        <div className="vcr-form" aria-label="Parcels sent">
          {c.shipments.slice(0, 10).map((s) => <Booking key={s.id} s={s} c={c} />)}
          {c.shipments.length > 10 && <p className="vcr-sub">Showing the latest 10 of {c.shipments.length}.</p>}
        </div>)}
      {open && <BookSheet c={c} tenantId={tenantId} onClose={() => setOpen(false)} />}
    </div>
  )
}
