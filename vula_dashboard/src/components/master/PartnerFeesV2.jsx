/**
 * PartnerFeesV2.jsx — Master › Partner fees (26 Nov 2026). A partner's fee on lessons taught by a
 * coach from another of its schools (vula/commerce/partner_fees.py): the usual rate, a school on
 * its own rate, how schools pay, and one month school by school — close a finished month into
 * statements, then mark each paid or waived. Rates are typed as a percentage and sent in
 * hundredths of a percent, read as written. Vula moves no money for this.
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, Lock, Save } from 'lucide-react'
import { Button, Card, Field, Input, Select, Skeleton, StatusChip, Textarea, toast } from '../ui/index.jsx'
import { authFetch } from '../../lib/authFetch'
import { formatMoney } from '../../lib/money'
import './master.css'

const PARTNER = 'flowcrew'
const TONE = { open: ['muted', 'Not closed'], due: ['warn', 'Due'], paid: ['ok', 'Paid'], waived: ['muted', 'Waived'] }
const send = (path, method, body) => authFetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
const pct = (bp) => (bp === null || bp === undefined ? '' : String(bp / 100))
/** "12.5" → 1250; null when it isn't a percentage with at most two decimals. */
const toBp = (text) => {
  const m = /^(\d{1,2})(?:[.,](\d{1,2}))?$/.exec(String(text ?? '').trim())
  return m ? Number(m[1]) * 100 + Number((m[2] || '').padEnd(2, '0')) : null
}
const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}` }

export default function PartnerFeesV2({ onError }) {
  const base = `/v1/master/partners/${PARTNER}`
  const [month, setMonth] = useState(thisMonth())
  const [d, setD] = useState(null)
  const [tenants, setTenants] = useState([])
  const [f, setF] = useState({ rate: '', note: '' })
  const [own, setOwn] = useState({ tenant: '', rate: '' })
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => authFetch(`${base}/fees?month=${month}`).then(x => {
    setD(x); setF({ rate: pct(x.settings.pool_fee_bp), note: x.settings.pay_note || '' })
  }).catch(e => onError(e.message)), [base, month, onError])
  useEffect(() => { setD(null); load() }, [load])
  useEffect(() => { authFetch('/v1/master/tenants').then(x => setTenants(x.tenants || [])).catch(() => {}) }, [])

  const run = async (work, done) => {
    setBusy(true)
    try { await work(); if (done) toast(done); await load() } catch (e) { onError(e.message) }
    setBusy(false)
  }
  const saveRate = () => {
    const bp = toBp(f.rate)
    if (bp === null) { onError('Type the fee as a percentage, like 10 or 12.5.'); return }
    run(() => send(`${base}/fee-settings`, 'PUT', { pool_fee_bp: bp, pay_note: f.note }), 'Saved. It applies to coaches asked from now on.')
  }
  const saveOwn = (tenant, text) => {
    const bp = text === null ? null : toBp(text)
    if (text !== null && bp === null) { onError('Type the fee as a percentage, like 10 or 12.5.'); return }
    run(() => send(`${base}/fee-settings`, 'PUT', { overrides: { [tenant]: bp } }), bp === null ? 'Back on the usual rate.' : 'Saved.')
      .then(() => setOwn({ tenant: '', rate: '' }))
  }
  const settle = (s, status) => {
    let note = ''
    if (status === 'waived') { note = window.prompt(`Why is ${s.school}'s fee waived?`) || ''; if (note.trim().length < 3) return }
    run(() => send(`${base}/fees/${month}/${s.tenant_id}/settle`, 'POST', { status, note }))
  }

  if (!d) return <Card><Skeleton lines={5} /></Card>
  const st = d.settings
  return (
    <div className="vms-form">
      <Card className="vms-form">
        <h3 className="vms-h">{st.name}'s fee on lessons taught by a guest coach</h3>
        <div className="vms-filters">
          <Field label="Usual fee (% of the lesson's price)"><Input inputMode="decimal" value={f.rate} style={{ width: 110 }} onChange={e => setF({ ...f, rate: e.target.value })} /></Field>
          <Button Icon={Save} disabled={busy} onClick={saveRate}>Save</Button>
        </div>
        <Field label="How a school pays (shown to schools with their statement)"><Textarea rows={2} maxLength={400} value={f.note} placeholder="EFT to FlowCrew, FNB 62…, use your school's name as the reference." onChange={e => setF({ ...f, note: e.target.value })} /></Field>
        <p className="vms-muted">{st.set ? `Set${st.updated_at ? ` on ${new Date(st.updated_at).toLocaleDateString('en-ZA')}` : ''}.` : `Not set yet, so the built-in ${pct(st.code_default_bp)}% applies.`} A lesson keeps the fee it was asked at, so a change reaches only coaches asked from now on. Between 0% and 30%.</p>
        <h3 className="vms-h">Schools on their own fee</h3>
        {st.overrides.length === 0 && <p className="vms-muted">Every school is on the usual fee.</p>}
        {st.overrides.map(o => (
          <div key={o.tenant_id} className="vms-row"><span className="vms-name">{o.school}</span><StatusChip tone="warn">{pct(o.fee_bp)}%</StatusChip>
            <Button size="sm" variant="quiet" disabled={busy} onClick={() => saveOwn(o.tenant_id, null)}>Back to the usual fee</Button></div>))}
        <div className="vms-filters">
          <Field label="School"><Select value={own.tenant} onChange={e => setOwn({ ...own, tenant: e.target.value })}>
            <option value="">Choose a school</option>{tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name || t.tenant_id}</option>)}</Select></Field>
          <Field label="Its fee (%)"><Input inputMode="decimal" value={own.rate} style={{ width: 90 }} onChange={e => setOwn({ ...own, rate: e.target.value })} /></Field>
          <Button variant="secondary" disabled={busy || !own.tenant || !own.rate.trim()} onClick={() => saveOwn(own.tenant, own.rate)}>Set its fee</Button>
        </div>
      </Card>

      <Card className="vms-form">
        <div className="vms-filters">
          <Field label="Month"><Input type="month" value={month} onChange={e => e.target.value && setMonth(e.target.value)} /></Field>
          {d.over && d.total.open_cents > 0 && <Button Icon={Lock} disabled={busy} onClick={() => run(() => send(`${base}/fees/${month}/close`, 'POST'), 'Closed. Each school has been told.')}>Close the month and tell the schools</Button>}
        </div>
        <p className="vms-summary">
          <StatusChip tone="muted">{d.total.lessons} {d.total.lessons === 1 ? 'lesson' : 'lessons'} · {formatMoney(d.total.value_cents)}</StatusChip>
          <StatusChip tone="ok">Fee {formatMoney(d.total.fee_cents)}</StatusChip>
          {d.total.due_cents > 0 && <StatusChip tone="warn">Due {formatMoney(d.total.due_cents)}</StatusChip>}
          {d.total.paid_cents > 0 && <StatusChip tone="ok">Paid {formatMoney(d.total.paid_cents)}</StatusChip>}
        </p>
        {d.schools.length === 0 && <p className="vms-muted">No lessons were taught by a guest coach and marked done in this month.</p>}
        {d.schools.map(s => {
          const [tone, word] = TONE[s.status] || TONE.open
          return (
            <div key={s.tenant_id} className="vms-row">
              <span className="vms-who"><span><span className="vms-name">{s.school}</span>
                <span className="vms-id">{s.lessons} {s.lessons === 1 ? 'lesson' : 'lessons'} · {formatMoney(s.value_cents)}{s.status !== 'open' && s.live_fee_cents !== s.fee_cents ? ` · lessons changed since: now ${formatMoney(s.live_fee_cents)}` : ''}{s.note ? ` · ${s.note}` : ''}</span></span></span>
              <span className="vula-money">{formatMoney(s.fee_cents)}</span>
              <StatusChip tone={tone}>{word}</StatusChip>
              {s.status === 'due' && <><Button size="sm" Icon={Check} disabled={busy} onClick={() => settle(s, 'paid')}>Mark paid</Button>
                <Button size="sm" variant="quiet" disabled={busy} onClick={() => settle(s, 'waived')}>Waive</Button></>}
              {(s.status === 'paid' || s.status === 'waived') && <Button size="sm" variant="quiet" disabled={busy} onClick={() => settle(s, 'due')}>Undo</Button>}
            </div>)
        })}
        <p className="vms-muted">{d.over ? 'Closing freezes each school’s fee as it stands and tells the school. A closed fee doesn’t change if a lesson is edited later.' : 'This month is still running. It can be closed once it is over.'} Vula takes no money for this: schools pay you directly and you mark it here.</p>
      </Card>
    </div>
  )
}
