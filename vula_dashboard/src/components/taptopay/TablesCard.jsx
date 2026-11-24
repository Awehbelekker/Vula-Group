/**
 * TablesCard.jsx — Money › Tap to Pay › Tables (24 Nov 2026): a restaurant's table tags, who is
 * serving each, and what customers can do from them (call a waiter / ask for the bill, and order
 * from the menu). Backend: vula_mind/vula/tap/tableside.py. A table's bill total is the server's.
 */
import { useCallback, useEffect, useState } from 'react'
import { Copy, ExternalLink, Plus, QrCode, Trash2, UtensilsCrossed } from 'lucide-react'
import { api } from '../../lib/api'
import { formatMoney } from '../../lib/money'
import { Button, Card, Field, Input, Select, Skeleton, confirmDialog, toast } from '../ui/index.jsx'

const copy = (t) => navigator.clipboard?.writeText(t).then(() => toast('Copied'), () => toast("Couldn't copy.", 'danger'))

export default function TablesCard({ tenantId }) {
  const [d, setD] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [add, setAdd] = useState({ label: '', waiter_id: '' })
  const base = `/v1/tap/${tenantId}/tables`

  const load = useCallback(() => api(base).then(setD).catch(e => setError(e.message)), [base])
  useEffect(() => { load() }, [load])

  const run = async (fn, done) => {
    setBusy(true); setError('')
    try { await fn(); if (done) toast(done); await load() } catch (e) { setError(e.message) }
    setBusy(false)
  }
  const setting = (patch) => run(() => api(`${base}/settings`, { method: 'PUT', body: patch }))

  if (!d) return <Card className="vtp-card">{error ? <p className="vtp-error" role="alert">{error}</p> : <Skeleton lines={3} />}</Card>
  const s = d.settings
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-tables">
      <h2 className="vtp-h" id="vtp-tables"><UtensilsCrossed size={18} aria-hidden="true" />Tables</h2>
      <p className="vtp-sub">Put a tag on each table. A customer who taps it can pay the table's bill, and, if you switch these on, call their waiter or order from your menu. The waiter is told in the staff app and on WhatsApp.</p>
      <label className="vtp-check"><input type="checkbox" checked={s.calls} disabled={busy} onChange={e => setting({ calls: e.target.checked })} />
        <span>Customers can call a waiter, ask for the bill, or ask for the card machine</span></label>
      <label className="vtp-check"><input type="checkbox" checked={s.orders} disabled={busy} onChange={e => setting({ orders: e.target.checked })} />
        <span>Customers can order from the menu<span className="vtp-muted">Your products in stock, at their prices. Nothing goes on the bill until a waiter confirms it.</span></span></label>
      {s.calls && (
        <Field label="Tell a manager when nobody has answered after" style={{ maxWidth: 320 }}>
          <Select value={s.escalate_minutes} disabled={busy} onChange={e => setting({ escalate_minutes: Number(e.target.value) })}>
            {[1, 2, 3, 5, 10, 15].map(n => <option key={n} value={n}>{n} minute{n === 1 ? '' : 's'}</option>)}
          </Select>
        </Field>
      )}
      {error && <p className="vtp-error" role="alert">{error}</p>}
      <ul className="vtp-list">
        {d.tables.map(t => (
          <li key={t.id} className="vtp-member">
            <span className="vtp-name"><strong>{t.label}</strong>{t.bill ? <span className="vtp-muted"> · bill {formatMoney(t.bill.total_cents, { cents: 'always' })}</span> : null}</span>
            <Field label="Served by" style={{ width: 170 }}>
              <Select value={t.waiter_id || ''} disabled={busy} onChange={e => run(() => api(`${base}/${t.id}/assign`, { method: 'POST', body: { waiter_id: e.target.value || null } }))}>
                <option value="">Nobody (managers are told)</option>
                {d.staff.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </Select>
            </Field>
            <Button size="sm" variant="secondary" Icon={Copy} onClick={() => copy(t.url)}>Copy link</Button>
            <a className="vtp-link" href={t.qr} target="_blank" rel="noreferrer"><QrCode size={14} aria-hidden="true" />QR code<ExternalLink size={12} aria-hidden="true" /></a>
            <Button size="sm" variant="quiet" Icon={Trash2} disabled={busy} aria-label={`Remove ${t.label}`}
              onClick={async () => { if (await confirmDialog(`Remove ${t.label}? Its tag stops working.`)) run(() => api(`${base}/${t.id}`, { method: 'DELETE' }), 'Removed') }} />
          </li>
        ))}
        {d.tables.length === 0 && <li className="vtp-muted">No tables yet. Add one, then print its QR code or write its link to an NFC sticker.</li>}
      </ul>
      <form className="vtp-row vtp-bottom" onSubmit={e => { e.preventDefault(); run(() => api(base, { method: 'POST', body: { label: add.label, waiter_id: add.waiter_id || null } }), 'Table added').then(() => setAdd({ label: '', waiter_id: '' })) }}>
        <Field label="New table" className="vtp-grow"><Input required maxLength={30} value={add.label} placeholder="Table 7" onChange={e => setAdd({ ...add, label: e.target.value })} /></Field>
        <Field label="Served by" style={{ width: 170 }}>
          <Select value={add.waiter_id} onChange={e => setAdd({ ...add, waiter_id: e.target.value })}>
            <option value="">Nobody yet</option>
            {d.staff.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </Select>
        </Field>
        <Button type="submit" Icon={Plus} disabled={busy}>Add table</Button>
      </form>
    </Card>
  )
}
