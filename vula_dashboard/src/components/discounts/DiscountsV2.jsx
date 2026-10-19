/**
 * DiscountsV2.jsx — the redesigned discount codes (2026-10-19, UX redesign Phase F). Same data and
 * actions as the current tab (discounts/useDiscounts.js): one card per code with an on/off switch,
 * and Add / Edit in a Sheet with every field labelled.
 */
import { Pencil, Plus, Tag, Trash2 } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, EmptyState, Field, Input, PageHeader, Select, Sheet, Skeleton, StatusChip } from '../ui/index.jsx'
import { useDiscounts } from './useDiscounts'
import '../delivery/sell.css'

const label = (c) => (c.type === 'percent' ? `${c.value}% off` : c.type === 'fixed' ? `${formatMoney(c.value, { cents: 'always' })} off` : 'Free delivery')

export default function DiscountsV2({ tenantId }) {
  const d = useDiscounts(tenantId)
  const f = d.form
  const set = (patch) => d.setForm({ ...f, ...patch })
  return (
    <div className="vsl">
      <PageHeader sub="Codes customers type at checkout — percent off, a fixed amount, or free delivery."
        actions={<Button Icon={Plus} onClick={d.startNew}>Add discount code</Button>} />
      {d.loading ? <Card><Skeleton lines={3} /></Card> : d.codes.length === 0 ? (
        <EmptyState Icon={Tag} title="No discount codes yet" action={<Button Icon={Plus} onClick={d.startNew}>Add your first code</Button>}>
          Create a code customers can type at checkout.
        </EmptyState>
      ) : (
        <ul className="vsl-grid" aria-label="Discount codes">
          {d.codes.map((c) => (
            <li key={c.id}>
              <Card className={`vsl-card${c.active ? '' : ' vsl-card--off'}`}>
                <div className="vsl-top">
                  <span className="vsl-code">{c.code}</span>
                  <StatusChip tone="ok">{label(c)}</StatusChip>
                  <button type="button" role="switch" aria-checked={!!c.active} className="vsl-switch" disabled={d.saving}
                    aria-label={`${c.code} is ${c.active ? 'on' : 'off'}`} onClick={() => d.toggleActive(c)}>
                    <span className="vsl-knob" aria-hidden="true" /><span>{c.active ? 'On' : 'Off'}</span>
                  </button>
                </div>
                <p className="vsl-sub">
                  {[c.min_order_cents ? `Min order ${formatMoney(c.min_order_cents, { cents: 'always' })}` : null,
                    `Used ${c.usage_count || 0}${c.usage_limit ? ` of ${c.usage_limit}` : ''}`,
                    c.starts_at ? `from ${c.starts_at.slice(0, 10)}` : null, c.ends_at ? `until ${c.ends_at.slice(0, 10)}` : null,
                    c.first_order_only ? 'first order only' : null, c.per_customer_limit ? `max ${c.per_customer_limit} per customer` : null,
                  ].filter(Boolean).join(' · ')}
                </p>
                <div className="vsl-actions">
                  <Button size="sm" variant="secondary" Icon={Pencil} onClick={() => d.startEdit(c)} aria-label={`Edit ${c.code}`}>Edit</Button>
                  <Button size="sm" variant="quiet" Icon={Trash2} disabled={d.saving} onClick={() => d.remove(c)} aria-label={`Delete ${c.code}`}>Delete</Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Sheet open={!!d.editing} title={d.editing?.id ? `Edit ${d.editing.code}` : 'New discount code'} onClose={d.cancel}>
        {d.editing && (
          <form className="vsl-form" onSubmit={d.save}>
            {d.error && <p className="vsl-error" role="alert">{d.error}</p>}
            <Field label="Code"><Input required placeholder="e.g. SUMMER20" value={f.code} onChange={(e) => set({ code: e.target.value.toUpperCase() })} /></Field>
            <div className="vsl-two">
              <Field label="Kind">
                <Select value={f.type} onChange={(e) => set({ type: e.target.value })}>
                  <option value="percent">Percent off</option><option value="fixed">Fixed amount off</option><option value="free_shipping">Free delivery</option>
                </Select>
              </Field>
              {f.type !== 'free_shipping' && (
                <Field label={f.type === 'fixed' ? 'Amount off (R)' : 'Percent off (1–100)'}>
                  <Input type="number" step={f.type === 'fixed' ? '0.01' : '1'} value={f.value} onChange={(e) => set({ value: e.target.value })} />
                </Field>)}
            </div>
            <div className="vsl-two">
              <Field label="Minimum order (R, optional)"><Input type="number" step="0.01" value={f.min_order_cents} onChange={(e) => set({ min_order_cents: e.target.value })} /></Field>
              <Field label="Usage limit (optional)"><Input type="number" value={f.usage_limit} onChange={(e) => set({ usage_limit: e.target.value })} /></Field>
            </div>
            <div className="vsl-two">
              <Field label="Starts (optional)"><Input type="date" value={f.starts_at} onChange={(e) => set({ starts_at: e.target.value })} /></Field>
              <Field label="Ends (optional)"><Input type="date" value={f.ends_at} onChange={(e) => set({ ends_at: e.target.value })} /></Field>
            </div>
            <Field label="Max uses per customer (optional)"><Input type="number" value={f.per_customer_limit} onChange={(e) => set({ per_customer_limit: e.target.value })} /></Field>
            <label className="vsl-check"><input type="checkbox" checked={f.first_order_only} onChange={(e) => set({ first_order_only: e.target.checked })} />First order only</label>
            <label className="vsl-check"><input type="checkbox" checked={f.active} onChange={(e) => set({ active: e.target.checked })} />On — customers can use it</label>
            <div className="vsl-actions">
              <Button type="submit" disabled={d.saving}>{d.saving ? 'Saving…' : d.editing.id ? 'Save changes' : 'Add code'}</Button>
              <Button type="button" variant="secondary" onClick={d.cancel}>Cancel</Button>
            </div>
          </form>
        )}
      </Sheet>
    </div>
  )
}
