/**
 * TablesV2.jsx — Sell › Tables (24 Nov 2026): a restaurant's bookings for the day, a booking
 * taken by phone, and the booking settings. Backend: vula_mind/vula/api/tables.py. Whether a table
 * is free is always the server's answer; this screen only shows it.
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, Copy, ExternalLink, Plus, Settings, Trash2, UtensilsCrossed } from 'lucide-react'
import { api } from '../../lib/api'
import { Button, Card, EmptyState, ErrorCard, Field, Input, PageHeader, Select, Sheet, Skeleton, StatusChip, confirmDialog, toast } from '../ui/index.jsx'
import './tables.css'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const STATE = { confirmed: ['muted', 'Booked'], seated: ['ok', 'Seated'], completed: ['ok', 'Done'], no_show: ['danger', 'No-show'], cancelled: ['danger', 'Cancelled'] }
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const shift = (day, n) => { const d = new Date(`${day}T12:00:00`); d.setDate(d.getDate() + n); return iso(d) }
const dayLabel = (day) => new Date(`${day}T12:00:00`).toLocaleDateString('en-ZA', { weekday: 'long', day: 'numeric', month: 'long' })
const timeOf = (when) => (when || '').split(' at ').pop()
const copy = (t) => navigator.clipboard?.writeText(t).then(() => toast('Copied'), () => toast("Couldn't copy.", 'danger'))

function SettingsSheet({ tenantId, st, onClose, onSaved }) {
  const [f, setF] = useState({ ...st, sittings: st.sittings.length ? st.sittings : [{ name: 'Dinner', days: [1, 2, 3, 4, 5, 6], start: '18:00', end: '22:00' }] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const num = (k) => (e) => setF({ ...f, [k]: e.target.value === '' ? '' : Number(e.target.value) })
  const setSit = (i, patch) => setF({ ...f, sittings: f.sittings.map((s, n) => n === i ? { ...s, ...patch } : s) })
  const save = async () => {
    setBusy(true); setError('')
    try {
      const d = await api(`/v1/commerce/${tenantId}/admin/tables/settings`, { method: 'PUT', body: {
        enabled: f.enabled, sittings: f.sittings, slot_minutes: Number(f.slot_minutes), seating_minutes: Number(f.seating_minutes),
        max_covers: Number(f.max_covers), max_party: Number(f.max_party), lead_minutes: Number(f.lead_minutes), horizon_days: Number(f.horizon_days), note: f.note || '' } })
      toast('Saved'); onSaved(d)
    } catch (e) { setError(e.message) }
    setBusy(false)
  }
  return (
    <Sheet open title="Booking settings" onClose={onClose} footer={<Button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>}>
      <div className="vtb-form">
        <label className="vtb-check"><input type="checkbox" checked={!!f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} /><span>Take bookings online and on WhatsApp</span></label>
        <h3 className="vtb-h">Sittings</h3>
        {f.sittings.map((s, i) => (
          <div key={i} className="vtb-sit">
            <div className="vtb-row">
              <Field label="Name"><Input value={s.name} maxLength={30} onChange={e => setSit(i, { name: e.target.value })} /></Field>
              <Field label="From"><Input type="time" value={s.start} onChange={e => setSit(i, { start: e.target.value })} /></Field>
              <Field label="Last booking before"><Input type="time" value={s.end} onChange={e => setSit(i, { end: e.target.value })} /></Field>
            </div>
            <div className="vtb-days" role="group" aria-label={`Days ${s.name || 'this sitting'} runs`}>
              {DAYS.map((d, n) => (
                <button key={d} type="button" aria-pressed={s.days.includes(n)} onClick={() => setSit(i, { days: s.days.includes(n) ? s.days.filter(x => x !== n) : [...s.days, n].sort() })}>{d}</button>
              ))}
              <Button type="button" size="sm" variant="quiet" Icon={Trash2} onClick={() => setF({ ...f, sittings: f.sittings.filter((_, n) => n !== i) })}>Remove</Button>
            </div>
          </div>
        ))}
        <div><Button type="button" size="sm" variant="secondary" Icon={Plus} onClick={() => setF({ ...f, sittings: [...f.sittings, { name: '', days: [0, 1, 2, 3, 4, 5, 6], start: '12:00', end: '15:00' }] })}>Add a sitting</Button></div>
        <h3 className="vtb-h">The room</h3>
        <div className="vtb-row">
          <Field label="People you can seat at once"><Input inputMode="numeric" value={f.max_covers} onChange={num('max_covers')} /></Field>
          <Field label="A table is kept for (minutes)"><Input inputMode="numeric" value={f.seating_minutes} onChange={num('seating_minutes')} /></Field>
          <Field label="Booking times every"><Select value={f.slot_minutes} onChange={num('slot_minutes')}><option value={15}>15 minutes</option><option value={30}>30 minutes</option><option value={60}>hour</option></Select></Field>
        </div>
        <div className="vtb-row">
          <Field label="Biggest group that can book itself"><Input inputMode="numeric" value={f.max_party} onChange={num('max_party')} /></Field>
          <Field label="Book at least this far ahead (minutes)"><Input inputMode="numeric" value={f.lead_minutes} onChange={num('lead_minutes')} /></Field>
          <Field label="Book up to (days ahead)"><Input inputMode="numeric" value={f.horizon_days} onChange={num('horizon_days')} /></Field>
        </div>
        <Field label="Note on the booking page (optional)"><Input value={f.note || ''} maxLength={200} placeholder="Kitchen closes at 21:30" onChange={e => setF({ ...f, note: e.target.value })} /></Field>
        {error && <p className="vtb-error" role="alert">{error}</p>}
      </div>
    </Sheet>
  )
}

function AddSheet({ tenantId, day, times, onClose, onDone }) {
  const [f, setF] = useState({ starts_at: times[0]?.starts_at || '', party: 2, name: '', phone: '', notes: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (e) => {
    e.preventDefault(); setBusy(true); setError('')
    try {
      await api(`/v1/commerce/${tenantId}/admin/tables`, { method: 'POST', body: { ...f, party: Number(f.party) } })
      toast('Booked'); onDone()
    } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <Sheet open title={`Add a booking for ${dayLabel(day)}`} onClose={onClose}>
      <form className="vtb-form" onSubmit={save}>
        <div className="vtb-row">
          <Field label="Time"><Select value={f.starts_at} onChange={e => setF({ ...f, starts_at: e.target.value })}>{times.map(t => <option key={t.starts_at} value={t.starts_at}>{t.time} · {t.sitting}</option>)}</Select></Field>
          <Field label="People"><Input inputMode="numeric" value={f.party} onChange={e => setF({ ...f, party: e.target.value })} /></Field>
        </div>
        <Field label="Name"><Input required value={f.name} maxLength={80} onChange={e => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Cellphone (optional)" hint="With a number, they get a WhatsApp reminder."><Input type="tel" value={f.phone} onChange={e => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="Note (optional)"><Input value={f.notes} maxLength={300} onChange={e => setF({ ...f, notes: e.target.value })} /></Field>
        {error && <p className="vtb-error" role="alert">{error}</p>}
        <div><Button type="submit" disabled={busy || !f.starts_at}>{busy ? 'Booking…' : 'Book the table'}</Button></div>
      </form>
    </Sheet>
  )
}

export default function TablesV2({ tenantId }) {
  const [day, setDay] = useState(iso(new Date()))
  const [sheet, setSheet] = useState(null)
  const [conf, setConf] = useState(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState('')            // '' | 'settings' | 'add'
  const base = `/v1/commerce/${tenantId}/admin/tables`

  const load = useCallback(() => {
    setError('')
    Promise.all([api(`${base}?date=${day}`), api(`${base}/settings`)]).then(([s, c]) => { setSheet(s); setConf(c) }).catch(e => setError(e.message))
  }, [base, day])
  useEffect(() => { load() }, [load])

  const mark = async (b, status, ask) => {
    if (ask && !await confirmDialog(ask)) return
    try { await api(`${base}/${b.id}/status`, { method: 'POST', body: { status } }); load() } catch (e) { toast(e.message, 'danger') }
  }

  if (error) return <ErrorCard what="your bookings" onRetry={load} />
  if (!sheet || !conf) return <Card><Skeleton lines={5} /></Card>
  const st = conf.settings
  return (
    <div className="vtb">
      <PageHeader sub="Table bookings from your booking page and WhatsApp, and the ones you take by phone."
        actions={<><Button variant="secondary" Icon={Settings} onClick={() => setOpen('settings')}>Settings</Button>
          {st.enabled && sheet.times.length > 0 && <Button Icon={Plus} onClick={() => setOpen('add')}>Add a booking</Button>}</>} />
      {!st.enabled ? (
        <EmptyState Icon={UtensilsCrossed} title="Bookings are off" action={<Button onClick={() => setOpen('settings')}>Set up bookings</Button>}>
          Set your sittings and how many people you seat, then customers can book on your page or by writing “book a table” on WhatsApp.
        </EmptyState>
      ) : (<>
        {conf.booking_url && (
          <Card className="vtb-link">
            <span className="vtb-sub">Your booking page</span><code>{conf.booking_url}</code>
            <span className="vtb-actions"><Button size="sm" variant="secondary" Icon={Copy} onClick={() => copy(conf.booking_url)}>Copy</Button>
              <a className="vtb-a" href={conf.booking_url} target="_blank" rel="noreferrer">Open<ExternalLink size={14} aria-hidden="true" /></a></span>
          </Card>
        )}
        <div className="vtb-daybar">
          <Button variant="quiet" Icon={ChevronLeft} aria-label="The day before" onClick={() => setDay(shift(day, -1))} />
          <label className="vtb-day"><span className="vtb-daytext">{dayLabel(day)}</span><input type="date" value={day} onChange={e => e.target.value && setDay(e.target.value)} aria-label="Choose a day" /></label>
          <Button variant="quiet" Icon={ChevronRight} aria-label="The day after" onClick={() => setDay(shift(day, 1))} />
          <span className="vtb-covers"><span className="vula-figure">{sheet.covers}</span> of {sheet.max_covers} seats booked</span>
        </div>
        {sheet.bookings.length === 0 ? <Card><p className="vtb-sub">No bookings for this day{sheet.times.length ? '' : ' — you have no sitting on it'}.</p></Card> : (
          <Card className="vtb-list">
            <ul>
              {sheet.bookings.map(b => {
                const [tone, label] = STATE[b.status] || STATE.confirmed
                return (
                  <li key={b.id} className={`vtb-b${b.status === 'cancelled' || b.status === 'no_show' ? ' vtb-b--off' : ''}`}>
                    <span className="vtb-time vula-money">{timeOf(b.when)}</span>
                    <span className="vtb-main"><strong>{b.customer_name} · {b.party_size}</strong>
                      <span className="vtb-sub">{[b.ref, b.customer_phone && `+${b.customer_phone}`, b.notes].filter(Boolean).join(' · ')}</span></span>
                    <StatusChip tone={tone}>{label}</StatusChip>
                    <span className="vtb-actions">
                      {b.status === 'confirmed' && <><Button size="sm" Icon={Check} onClick={() => mark(b, 'seated')}>Seated</Button>
                        <Button size="sm" variant="quiet" onClick={() => mark(b, 'no_show')}>No-show</Button>
                        <Button size="sm" variant="quiet" onClick={() => mark(b, 'cancelled', `Cancel ${b.customer_name}'s booking? Their seats open up again.`)}>Cancel</Button></>}
                      {b.status === 'seated' && <Button size="sm" variant="secondary" onClick={() => mark(b, 'completed')}>Table free</Button>}
                      {(b.status === 'no_show' || b.status === 'cancelled') && <Button size="sm" variant="quiet" onClick={() => mark(b, 'confirmed')}>Undo</Button>}
                    </span>
                  </li>
                )
              })}
            </ul>
          </Card>
        )}
      </>)}
      {open === 'settings' && <SettingsSheet tenantId={tenantId} st={st} onClose={() => setOpen('')} onSaved={(d) => { setConf(d); setOpen(''); load() }} />}
      {open === 'add' && <AddSheet tenantId={tenantId} day={day} times={sheet.times} onClose={() => setOpen('')} onDone={() => { setOpen(''); load() }} />}
    </div>
  )
}
