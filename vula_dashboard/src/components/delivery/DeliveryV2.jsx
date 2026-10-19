/**
 * DeliveryV2.jsx — the redesigned Delivery tab (2026-10-19, UX redesign Phase F). Same data and
 * actions as the current tab (delivery/useDelivery.js): the day's deliveries by slot with a
 * printable run sheet, and the order-workflow settings (approval, dispatch, collection, payment
 * options) in a card below.
 */
import { CalendarDays, MapPin, Printer, Truck } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, EmptyState, Field, Input, PageHeader, Select, Skeleton, StatCard, StatusChip, Textarea } from '../ui/index.jsx'
import { DEL_PAID, PAYMENT_OPTIONS, SLOT_NAMES, printRunSheet, useDeliveryList, useOrderWorkflow } from './useDelivery'
import './sell.css'

const money = (c) => formatMoney(c || 0, { cents: 'always' })
const slotLabel = (slot) => SLOT_NAMES[slot] || (slot === 'unscheduled' ? 'No time slot' : slot)

export default function DeliveryV2({ tenantId }) {
  const d = useDeliveryList(tenantId)
  return (
    <div className="vsl">
      <PageHeader sub="The day's deliveries by time slot, and a run sheet to print for the driver."
        actions={d.orders.length > 0 && <Button Icon={Printer} onClick={() => printRunSheet({ date: d.date, slots: d.slots, bySlot: d.bySlot, slotLabel })}>Print run sheet</Button>} />
      <div className="vsl-datebar">
        <Field label="Day"><Input type="date" value={d.date} onChange={(e) => d.setDate(e.target.value)} /></Field>
        <Button variant="secondary" Icon={CalendarDays} onClick={() => d.setDate(d.today)} disabled={d.date === d.today}>Today</Button>
      </div>

      {!d.loading && d.data && (
        <div className="vsl-stats">
          <StatCard variant="tile" label="Deliveries" value={d.data.total} sub={`${d.slots.length} slot${d.slots.length !== 1 ? 's' : ''}`} />
          <StatCard variant="tile" label="Paid" value={d.data.paid_count} sub={money(d.data.paid_revenue_cents)} color="var(--ok-text)" />
          <StatCard variant="tile" label="Unpaid" value={d.data.unpaid_count} sub={money(d.data.unpaid_revenue_cents)} color={d.data.unpaid_count ? 'var(--warn-text)' : undefined} />
          <StatCard variant="tile" label="To collect" value={money((d.data.paid_revenue_cents || 0) + (d.data.unpaid_revenue_cents || 0))} sub="total value" />
        </div>)}

      {d.loading && <Card><Skeleton lines={3} /></Card>}
      {!d.loading && d.orders.length === 0 && <EmptyState Icon={Truck} title="No deliveries">Nothing is scheduled for {d.date}.</EmptyState>}

      {d.slots.map((slot) => (
        <section key={slot} className="vsl-slot" aria-labelledby={`vsl-slot-${slot}`}>
          <h2 id={`vsl-slot-${slot}`} className="vsl-slot-h">{slotLabel(slot)}<span className="vsl-sub"> · {d.bySlot[slot].length}</span></h2>
          <ul className="vsl-grid">
            {d.bySlot[slot].map((o) => {
              const paid = DEL_PAID.has(o.status)
              const items = o.commerce_order_items || []
              return (
                <li key={o.id}>
                  <Card className="vsl-card">
                    <div className="vsl-top">
                      <span className="vsl-code">{o.display_id}</span>
                      <StatusChip tone={paid ? 'ok' : 'warn'}>{paid ? 'Paid' : 'Unpaid'}</StatusChip>
                      <span className="vula-money vsl-amt">{money(o.total_cents)}</span>
                    </div>
                    <p className="vsl-body">{o.customer_name}<span className="vsl-sub"> · {o.customer_phone}</span></p>
                    <p className="vsl-addr"><MapPin size={16} aria-hidden="true" />{o.delivery_address || 'No address'}</p>
                    {o.delivery_notes && <p className="vsl-sub">Note: {o.delivery_notes}</p>}
                    {items.length > 0 && <p className="vsl-sub">{items.map((i) => `${i.product_name || 'Item'} × ${i.quantity}`).join(' · ')}</p>}
                  </Card>
                </li>)
            })}
          </ul>
        </section>
      ))}

      <Workflow tenantId={tenantId} />
    </div>
  )
}

function Workflow({ tenantId }) {
  const w = useOrderWorkflow(tenantId)
  const s = w.s
  if (!s) return null
  return (
    <Card className="vsl-card vsl-workflow">
      <div className="vsl-top">
        <h2 className="vsl-slot-h">Order workflow</h2>
        {w.msg && <span className="vsl-msg" role="status">Saved</span>}
      </div>
      <p className="vsl-sub">Approve orders before fulfilment, choose where the ticket goes, and how customers can pay.</p>
      <label className="vsl-check"><input type="checkbox" checked={!!s.require_approval} onChange={(e) => w.save({ require_approval: e.target.checked })} />
        Ask the owner to approve each order on WhatsApp before it's fulfilled</label>
      <div className="vsl-three">
        <Field label="Send the fulfilment ticket by">
          <Select value={s.dispatch_channel || 'whatsapp'} onChange={(e) => w.save({ dispatch_channel: e.target.value })}>
            <option value="whatsapp">WhatsApp</option><option value="email">Email</option><option value="both">Both</option><option value="none">Don't send</option>
          </Select>
        </Field>
        <Field label="Fulfilment WhatsApp number"><Input type="tel" placeholder="2782…" defaultValue={s.fulfillment_whatsapp || ''}
          onBlur={(e) => e.target.value !== (s.fulfillment_whatsapp || '') && w.save({ fulfillment_whatsapp: e.target.value })} /></Field>
        <Field label="Fulfilment email"><Input type="email" placeholder="kitchen@…" defaultValue={s.fulfillment_email || ''}
          onBlur={(e) => e.target.value !== (s.fulfillment_email || '') && w.save({ fulfillment_email: e.target.value })} /></Field>
      </div>
      <div className="vsl-divide">
        <label className="vsl-check"><input type="checkbox" checked={!!s.collection_enabled} onChange={(e) => w.save({ collection_enabled: e.target.checked })} />
          Offer collection — no delivery fee, no address needed</label>
        {s.collection_enabled && (
          <Field label="Where and when to collect"><Input placeholder="e.g. Shop, 12 Main Rd — weekdays 9–5" defaultValue={s.collection_note || ''}
            onBlur={(e) => e.target.value !== (s.collection_note || '') && w.save({ collection_note: e.target.value })} /></Field>)}
      </div>
      <fieldset className="vsl-divide vsl-fieldset">
        <legend className="vsl-legend">How customers can pay when ordering on WhatsApp</legend>
        <div className="vsl-row">
          {PAYMENT_OPTIONS.map(([key, l]) => (
            <label key={key} className="vsl-check"><input type="checkbox" checked={w.methods.includes(key)} onChange={(e) => w.toggleMethod(key, e.target.checked)} />{l}</label>))}
        </div>
        {w.methods.includes('eft') && (
          <Field label="Bank details shown to customers who choose EFT">
            <Textarea rows={4} defaultValue={s.eft_details || ''} placeholder={'Bank: FNB\nAccount name: …\nAccount no: …\nBranch: …'}
              onBlur={(e) => e.target.value !== (s.eft_details || '') && w.save({ eft_details: e.target.value })} />
          </Field>)}
      </fieldset>
    </Card>
  )
}
