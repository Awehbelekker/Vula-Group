/**
 * SuppliersV2.jsx — the redesigned Operate › Suppliers (2026-10-20, UX redesign Phase F). Same
 * calls as SuppliersTab (suppliers/useSuppliers.js): the supplier list with an add/edit sheet
 * (payment terms, VAT number and aliases feed bill auto-matching), then purchase orders — the
 * low-stock reorder suggestions, and each order's send / mark sent / received / cancel.
 * Amounts are the server's integer cents through formatMoney.
 */
import { Ban, Bell, CheckCircle2, ClipboardList, Pencil, Plus, Receipt, Send, Trash2, Truck } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, DataTable, EmptyState, Field, IconButton, Input, Select, Sheet, Skeleton, StatusChip, Textarea } from '../ui/index.jsx'
import { usePurchaseOrders, useSuppliers } from './useSuppliers'
import './suppliers.css'

const PO_TONE = { draft: 'muted', sent: 'accent', received: 'ok', cancelled: 'muted' }
const PO_LABEL = { draft: 'Draft', sent: 'Sent', received: 'Received', cancelled: 'Cancelled' }
const contactLine = (s) => [
  s.tax_id ? `VAT ${s.tax_id}` : null,
  s.contact_phone || null,
  s.contact_email || null,
  (s.aliases && s.aliases.length) ? `aka ${Array.isArray(s.aliases) ? s.aliases.join(', ') : s.aliases}` : null,
].filter(Boolean).join(' · ') || '—'
const terms = (s) => `${s.payment_terms_days ?? 30} day terms`

export default function SuppliersV2({ tenantId, onViewInvoices }) {
  const x = useSuppliers(tenantId)
  const f = x.form
  const set = (k) => (e) => x.setForm({ ...f, [k]: e.target.value })
  const existing = !!x.editing?.id

  return (
    <section className="vsp" aria-label="Suppliers">
      {/* the skeleton only on the first load: a refresh after a save keeps the list in place */}
      {x.loading && !x.suppliers.length ? <Card><Skeleton lines={4} /></Card> : (
        <>
          <div className="vsp-head">
            <h2 className="vsp-h2">Suppliers</h2>
            {x.suppliers.length > 0 && <Button Icon={Plus} onClick={x.startNew}>Add supplier</Button>}
          </div>
          {x.suppliers.length === 0
            ? <EmptyState Icon={Truck} title="Set up your suppliers"
                action={<Button Icon={Plus} onClick={x.startNew}>Add your first supplier</Button>}>
                Add suppliers once and Vula auto-fills payment terms, VAT and account details when you scan their bills,
                and matches incoming invoices automatically.
              </EmptyState>
            : <DataTable caption="Suppliers" rows={x.suppliers} onRowClick={x.startEdit}
                columns={[
                  { key: 'name', label: 'Supplier', render: s => <b>{s.name}</b> },
                  { key: 'payment_terms_days', label: 'Terms', render: terms },
                  { key: 'contact', label: 'Details', render: s => <span className="vsp-muted">{contactLine(s)}</span> },
                  { key: 'actions', label: '', align: 'right', render: s => (
                    <span className="vsp-icons">
                      {onViewInvoices && <IconButton quiet Icon={Receipt} label={`Invoices from ${s.name}`} onClick={e => { e.stopPropagation(); onViewInvoices(s.id) }} />}
                      <IconButton quiet Icon={Pencil} label={`Edit ${s.name}`} onClick={e => { e.stopPropagation(); x.startEdit(s) }} />
                      <IconButton quiet Icon={Trash2} label={`Delete ${s.name}`} className="vsp-danger" disabled={x.saving}
                        onClick={e => { e.stopPropagation(); x.remove(s) }} />
                    </span>) },
                ]}
                stack={{ title: s => s.name, detail: contactLine, trailing: terms }} />}
        </>)}

      <Sheet open={!!x.editing} title={existing ? `Edit ${x.editing.name}` : 'Add supplier'} onClose={x.cancel}
        footer={
          <div className="vsp-actions">
            <Button type="submit" form="vsp-form" Icon={CheckCircle2} disabled={x.saving}>
              {x.saving ? 'Saving…' : existing ? 'Save changes' : 'Add supplier'}</Button>
            <Button variant="secondary" onClick={x.cancel}>Cancel</Button>
          </div>}>
        <form id="vsp-form" className="vsp-form" onSubmit={x.save}>
          <Field label="Supplier name"><Input value={f.name} required onChange={set('name')} /></Field>
          <div className="vsp-grid">
            <Field label="Payment terms (days)"><Input type="number" min="0" inputMode="numeric" value={f.payment_terms_days} onChange={set('payment_terms_days')} /></Field>
            <Field label="Tax / VAT number"><Input value={f.tax_id || ''} onChange={set('tax_id')} /></Field>
            <Field label="Phone"><Input type="tel" value={f.contact_phone || ''} onChange={set('contact_phone')} /></Field>
            <Field label="Email"><Input type="email" value={f.contact_email || ''} onChange={set('contact_email')} /></Field>
          </div>
          <Field label="Other names on their bills" hint="Comma-separated. The VAT number and these names improve auto-matching when you scan this supplier's bills.">
            <Input value={f.aliases} onChange={set('aliases')} />
          </Field>
          <Field label="Notes" hint="Optional"><Textarea rows={2} value={f.notes || ''} onChange={set('notes')} /></Field>
          {existing && (
            <div className="vsp-actions">
              {onViewInvoices && <Button variant="secondary" size="sm" Icon={Receipt} type="button" onClick={() => onViewInvoices(x.editing.id)}>Invoices</Button>}
              <Button variant="secondary" size="sm" Icon={Trash2} type="button" className="vsp-danger" disabled={x.saving}
                onClick={async () => { if (await x.remove(x.editing)) x.cancel() }}>Delete supplier</Button>
            </div>)}
        </form>
      </Sheet>

      <PurchaseOrdersV2 tenantId={tenantId} />
    </section>
  )
}

function PurchaseOrdersV2({ tenantId }) {
  const p = usePurchaseOrders(tenantId)
  if (p.loading && !p.pos.length && !p.suggestions.length) return <Card><Skeleton lines={3} /></Card>
  return (
    <section className="vsp" aria-labelledby="vsp-po">
      <h2 className="vsp-h2" id="vsp-po">Purchase orders</h2>

      {p.suggestions.length > 0 && (
        <Card className="vsp-card vsp-suggest">
          <h3 className="vsp-h"><Bell size={16} aria-hidden="true" />Low stock: suggested reorders</h3>
          <ul className="vsp-list">
            {p.suggestions.map((g, i) => {
              const key = g.supplier_id || 'unassigned'
              return (
                <li key={i} className="vsp-item">
                  <div className="vsp-row">
                    <b className="vsp-grow">{g.supplier_name}</b>
                    <Button size="sm" Icon={Plus} disabled={p.busy === key} onClick={() => p.createFromSuggestion(g)}>
                      {p.busy === key ? 'Creating…' : 'Create PO'}</Button>
                  </div>
                  <p className="vsp-muted">{g.items.map(it => `${it.name} (${it.stock_quantity} left → order ${it.suggested_qty})`).join(' · ')}</p>
                </li>)
            })}
          </ul>
        </Card>)}

      {p.pos.length === 0
        ? <EmptyState Icon={ClipboardList} title="No purchase orders yet">Set a reorder threshold on a product to get suggestions.</EmptyState>
        : <ul className="vsp-list vsp-pos" aria-label="Purchase orders">
            {p.pos.map(po => {
              const busy = p.busy === po.id
              const msg = p.sendMsg[po.id]
              return (
                <li key={po.id}>
                  <Card className="vsp-card">
                    <div className="vsp-row">
                      <b className="vsp-grow">{po.supplier_name || 'Unassigned'}{po.po_number && <span className="vsp-muted"> · {po.po_number}</span>}</b>
                      <StatusChip tone={PO_TONE[po.status] || 'muted'}>{PO_LABEL[po.status] || po.status}</StatusChip>
                      <span className="vula-money">{formatMoney(po.total_cents || 0, { cents: 'always' })}</span>
                    </div>
                    <p className="vsp-muted">{(po.items || []).map(it => `${it.name} ×${it.quantity}`).join(' · ')}
                      {po.sent_channel && ` · sent via ${po.sent_channel}`}</p>
                    {(po.status === 'draft' || po.status === 'sent') && (
                      <div className="vsp-row">
                        {po.status === 'draft' && (
                          <>
                            <Select value={p.channel[po.id] || 'email'} aria-label={`Send ${po.supplier_name || 'this order'} by`} className="vsp-channel"
                              onChange={e => p.setChannel({ ...p.channel, [po.id]: e.target.value })}>
                              <option value="email">Email</option>
                              <option value="whatsapp">WhatsApp</option>
                              <option value="both">Both</option>
                            </Select>
                            <Button size="sm" Icon={Send} disabled={busy} onClick={() => p.sendPo(po)}>Send</Button>
                            <Button size="sm" variant="secondary" disabled={busy} onClick={() => p.advance(po, 'sent')}
                              title="Mark sent without Vula dispatching it — e.g. you phoned the order in">Mark sent manually</Button>
                          </>)}
                        {po.status === 'sent' && <Button size="sm" Icon={CheckCircle2} disabled={busy} onClick={() => p.advance(po, 'received')}>Mark received</Button>}
                        <Button size="sm" variant="secondary" Icon={Ban} className="vsp-danger" disabled={busy} onClick={() => p.advance(po, 'cancelled')}>Cancel</Button>
                      </div>)}
                    {msg && <p className={msg.includes('error') || msg === 'network' ? 'vsp-error' : 'vsp-muted'} role="status">{msg.replace(/\s*✓$/, '')}</p>}
                  </Card>
                </li>)
            })}
          </ul>}
    </section>
  )
}
