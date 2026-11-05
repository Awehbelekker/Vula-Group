/**
 * BuyingV2.jsx — Settings › Buying (5 Nov 2026). The owner decides whether Vula may buy for the
 * business, the limits, who may ask and who approves, and the shops it may buy from. On WhatsApp
 * someone asks ("find ink for the Canon PG-545"), Vula shows the prices printed on these shops'
 * pages, and nothing is bought until an approver says yes: then the approver gets the shop's link
 * to pay (card, Google Pay or Apple Pay), or a purchase order goes to a supplier on account.
 * Vula never stores a card or a shop password. Data: connect/useBuying.js.
 */
import { useState } from 'react'
import { Plus, ShoppingCart, Trash2 } from 'lucide-react'
import { Button, Card, ErrorCard, Field, Input, Select, Skeleton, StatusChip } from '../../ui/index.jsx'
import { useBuying } from './useBuying'
import '../settings.css'

const WHO = { owner: 'Only the approvers', managers: 'Managers too', team: 'Anyone on the team' }
const STATUS = {
  pending_approval: ['warn', 'Waiting for approval'], approved: ['ok', 'Approved — to pay'],
  ordered: ['ok', 'Ordered'], paid: ['ok', 'Paid'], rejected: ['muted', 'Rejected'], cancelled: ['muted', 'Not bought'],
}
const money = c => `R${((c || 0) / 100).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function BuyingV2({ tenantId }) {
  const x = useBuying(tenantId)
  if (x.failed) return <ErrorCard what="buying" onRetry={x.reload} />
  if (!x.data || !x.form) return <Card><Skeleton lines={4} /></Card>
  const { form, data } = x
  const people = (data.approvers || []).map(a => a.name || a.phone).join(', ')
  return (
    <>
      <Card>
        <form className="vst-card" onSubmit={x.save}>
          <p className="vst-body">
            Ask on WhatsApp — “find ink cartridges for the Canon PG-545” — and Vula compares the prices on the shops below.
            Nothing is bought until an approver says yes. Then the approver gets the shop’s link to pay (card, Google Pay or
            Apple Pay), or a purchase order goes to a supplier you have an account with. Vula never stores a card or a shop password.
          </p>
          <label className="vst-check"><input type="checkbox" checked={form.enabled} onChange={e => x.set({ enabled: e.target.checked })} />Let Vula find and buy things for the business</label>
          <div className="vst-grid">
            <Field label="Most per purchase (R)" hint="Required before buying can be switched on">
              <Input inputMode="decimal" value={form.per_purchase_limit} placeholder="1500" onChange={e => x.set({ per_purchase_limit: e.target.value })} />
            </Field>
            <Field label="Most per month (R)" hint={`Spent this month: ${money(data.month_spent_cents)}. Leave empty for no monthly cap.`}>
              <Input inputMode="decimal" value={form.monthly_limit} placeholder="5000" onChange={e => x.set({ monthly_limit: e.target.value })} />
            </Field>
          </div>
          <Field label="Who may ask Vula to buy">
            <Select value={form.who_can_ask} onChange={e => x.set({ who_can_ask: e.target.value })}>
              {Object.entries(WHO).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </Select>
          </Field>
          <Field label="Who approves" hint={`WhatsApp numbers, separated by commas. Empty = the owners and admins${people ? ` (now: ${people})` : ''}.`}>
            <Input value={form.approver_phones} placeholder="082 000 0000" onChange={e => x.set({ approver_phones: e.target.value })} />
          </Field>
          <Field label="Book purchases to" hint="A project or Overheads, unless the request names a project">
            <Input value={form.default_project} placeholder="Overheads" onChange={e => x.set({ default_project: e.target.value })} />
          </Field>
          {x.error && <p className="vcn-error" role="alert">{x.error}</p>}
          <div className="vst-row"><Button type="submit" disabled={x.busy}>{x.busy ? 'Saving…' : 'Save'}</Button></div>
        </form>
      </Card>
      <Shops x={x} />
      <Purchases rows={data.purchases || []} />
    </>
  )
}

function Shops({ x }) {
  const [shop, setShop] = useState({ name: '', domain: '', how: 'checkout_link', supplier_id: '' })
  async function add(e) {
    e.preventDefault()
    const body = shop.how === 'account' ? { name: shop.name, how: 'account', supplier_id: shop.supplier_id }
      : { name: shop.name, how: 'checkout_link', domain: shop.domain }
    if (await x.addShop(body)) setShop({ name: '', domain: '', how: 'checkout_link', supplier_id: '' })
  }
  const shops = x.data.shops || []
  return (
    <Card className="vst-card">
      <span className="vst-strong">Shops Vula may buy from</span>
      {shops.length === 0 ? <p className="vst-body">None yet — Vula only ever searches the shops on this list.</p> : (
        <ul className="vcn-list" aria-label="Allowed shops">
          {shops.map(s => (
            <li key={s.id} className="vcn-item">
              <div className="vcn-item-main">
                <span className="vst-strong">{s.name}</span>
                <span className="vst-sub">{s.how === 'account' ? 'Order on account (purchase order)' : `Online: ${s.domain} — the approver pays at checkout`}</span>
              </div>
              <Button size="sm" variant="quiet" Icon={Trash2} className="vcn-danger-btn" onClick={() => x.removeShop(s)} aria-label={`Remove ${s.name}`}>Remove</Button>
            </li>
          ))}
        </ul>
      )}
      <form className="vst-card" onSubmit={add}>
        <div className="vst-grid">
          <Field label="Shop name"><Input required value={shop.name} placeholder="Office supplies shop" onChange={e => setShop({ ...shop, name: e.target.value })} /></Field>
          <Field label="How you buy there">
            <Select value={shop.how} onChange={e => setShop({ ...shop, how: e.target.value })}>
              <option value="checkout_link">Online shop (pay by link)</option>
              <option value="account" disabled={!x.suppliers.length}>A supplier on account</option>
            </Select>
          </Field>
        </div>
        {shop.how === 'account' ? (
          <Field label="Which supplier">
            <Select required value={shop.supplier_id} onChange={e => setShop({ ...shop, supplier_id: e.target.value })}>
              <option value="">Choose…</option>
              {x.suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </Field>
        ) : (
          <Field label="Website" hint="Only this site is searched, e.g. shop.example.co.za">
            <Input required value={shop.domain} placeholder="shop.example.co.za" onChange={e => setShop({ ...shop, domain: e.target.value })} />
          </Field>
        )}
        <div className="vst-row"><Button type="submit" size="sm" Icon={Plus} disabled={x.busy}>Add shop</Button></div>
      </form>
    </Card>
  )
}

function Purchases({ rows }) {
  if (!rows.length) return null
  return (
    <Card className="vst-card">
      <span className="vst-strong">Recent purchases</span>
      <ul className="vcn-list" aria-label="Recent purchases">
        {rows.map(p => {
          const [tone, label] = STATUS[p.status] || ['muted', p.status]
          const item = (p.items || [])[0] || {}
          return (
            <li key={p.id} className="vcn-item">
              <div className="vcn-item-main">
                <span className="vst-row"><ShoppingCart size={14} aria-hidden="true" /><span className="vst-strong">{item.quantity} × {item.name}</span><StatusChip tone={tone}>{label}</StatusChip></span>
                <span className="vst-sub">{p.shop_name} · {p.total_cents ? money(p.total_cents) : 'price from the supplier'}{p.project ? ` · ${p.project}` : ''}
                  {p.requested_name ? ` · asked by ${p.requested_name}` : ''}{p.approved_by ? ` · ${p.status === 'rejected' ? 'rejected' : 'approved'} by ${p.approved_by}` : ''}</span>
                {p.notes && p.status === 'cancelled' && <span className="vst-sub">{p.notes}</span>}
              </div>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
