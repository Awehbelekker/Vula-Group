/**
 * ShiftsV2.jsx — Admin › Shifts (24 Nov 2026): the manager's week. Plan who works when, see what
 * each person said they can work, publish, and deal with "can't make it" and "sick". Backend:
 * vula_mind/vula/tap/shifts.py. Hours and warnings are the server's; only an owner or a manager can
 * change anything (the server refuses others).
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Copy, Megaphone, Plus, Send, Trash2 } from 'lucide-react'
import { api } from '../../lib/api'
import { Button, Card, ErrorCard, Field, Input, PageHeader, Select, Sheet, Skeleton, StatusChip, confirmDialog, toast } from '../ui/index.jsx'
import './shifts.css'

const STATE = { draft: ['muted', 'Draft'], published: ['warn', 'Waiting to confirm'], confirmed: ['ok', 'Confirmed'], declined: ['danger', "Can't make it"], sick: ['danger', 'Off sick'] }
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const shiftDay = (day, n) => { const d = new Date(`${day}T12:00:00`); d.setDate(d.getDate() + n); return iso(d) }
const dayLabel = (day) => new Date(`${day}T12:00:00`).toLocaleDateString('en-ZA', { weekday: 'long', day: 'numeric', month: 'short' })
const hhmm = (when) => (when || '').split(', ').pop()

function ShiftSheet({ tenantId, week, edit, onClose, onDone }) {
  const s = edit.shift
  const [f, setF] = useState({ member_id: s?.member_id || '', date: s?.date || edit.date, start: s ? hhmm(s.when).split('–')[0] : '17:00',
    end: s ? hhmm(s.when).split('–')[1] : '23:00', section: s?.section || '', note: s?.note || '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const person = week.team.find(m => m.id === f.member_id)
  const save = async (e) => {
    e.preventDefault(); setBusy(true); setError('')
    // The times are sent as written with no zone: the server reads them as South African time.
    const body = { member_id: f.member_id || null, starts_at: `${f.date}T${f.start}:00`, ends_at: `${f.date}T${f.end}:00`, section: f.section, note: f.note }
    try {
      const d = await api(`/v1/tap/${tenantId}/shifts${s ? `/${s.id}` : ''}`, { method: s ? 'PUT' : 'POST', body })
      if (d.shift?.warning) toast(`Saved. Note: ${d.shift.name} ${d.shift.warning}.`); else toast('Saved')
      onDone()
    } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <Sheet open title={s ? 'Change the shift' : 'Add a shift'} onClose={onClose}>
      <form className="vsh-form" onSubmit={save}>
        <Field label="Who"><Select value={f.member_id} onChange={e => setF({ ...f, member_id: e.target.value })}>
          <option value="">Open — anyone on the team can take it</option>
          {week.team.map(m => <option key={m.id} value={m.id}>{m.name} · {m.hours} h this week</option>)}
        </Select></Field>
        {person && (
          <p className="vsh-sub">{person.availability.weekly.length
            ? `Can work: ${person.availability.weekly.map(r => `${DAYS[r.weekday]} ${r.start}–${r.end}`).join(', ')}.`
            : 'Hasn’t said when they can work.'}{person.availability.off.length ? ` Away: ${person.availability.off.map(r => (r.from === r.to ? r.from : `${r.from} to ${r.to}`)).join(', ')}.` : ''}</p>
        )}
        <div className="vsh-row">
          <Field label="Day"><Input type="date" required value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field>
          <Field label="From"><Input type="time" required value={f.start} onChange={e => setF({ ...f, start: e.target.value })} /></Field>
          <Field label="Until"><Input type="time" required value={f.end} onChange={e => setF({ ...f, end: e.target.value })} /></Field>
        </div>
        <Field label="Section (optional)"><Input value={f.section} maxLength={40} placeholder="Floor, Bar, Tables 1–6" onChange={e => setF({ ...f, section: e.target.value })} /></Field>
        <Field label="Note for them (optional)"><Input value={f.note} maxLength={120} onChange={e => setF({ ...f, note: e.target.value })} /></Field>
        {s && s.status !== 'draft' && <p className="vsh-sub">This shift is already published. Saving tells them about the change and asks them to confirm again.</p>}
        {error && <p className="vsh-error" role="alert">{error}</p>}
        <div><Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button></div>
      </form>
    </Sheet>
  )
}

export default function ShiftsV2({ tenantId }) {
  const [day, setDay] = useState(iso(new Date()))
  const [week, setWeek] = useState(null)
  const [error, setError] = useState('')
  const [edit, setEdit] = useState(null)             // null | { date, shift? }
  const [busy, setBusy] = useState(false)
  const base = `/v1/tap/${tenantId}/shifts`

  const load = useCallback(() => {
    setError('')
    api(`${base}?week=${day}`).then(setWeek).catch(e => setError(e.message))
  }, [base, day])
  useEffect(() => { load() }, [load])

  const run = async (fn, done) => {
    setBusy(true)
    try { await fn(); if (done) toast(done); load() } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  const publish = async () => {
    if (!await confirmDialog(`Publish ${week.drafts} shift${week.drafts === 1 ? '' : 's'}? Each person is told on the staff app and WhatsApp.`)) return
    run(() => api(`${base}/publish?week=${day}`, { method: 'POST', body: {} }), 'Published')
  }

  const copy = async () => {
    setBusy(true)
    try {
      const r = await api(`${base}/copy?week=${day}`, { method: 'POST', body: {} })
      const left = [r.already_there && `${r.already_there} already planned`, r.left_the_team && `${r.left_the_team} for people no longer on the team`].filter(Boolean).join(', ')
      toast(!r.last_week ? 'There were no shifts the week before.'
        : `${r.copied} shift${r.copied === 1 ? '' : 's'} copied as drafts${left ? ` (left out: ${left})` : ''}.`, r.copied ? undefined : 'warn')
      load()
    } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }

  if (error) return <ErrorCard what="the shifts" onRetry={load} />
  if (!week) return <Card><Skeleton lines={6} /></Card>
  const cover = week.shifts.filter(s => s.status === 'declined' || s.status === 'sick')
  return (
    <div className="vsh">
      <PageHeader sub="Plan who works when, then publish. Each person confirms on the staff app or WhatsApp, and you hear at once if someone can’t make it or is sick."
        actions={<>
          <Button variant="secondary" Icon={Copy} disabled={busy} onClick={copy}
            title="Add the week before's shifts to this week as drafts. Nobody is told until you publish.">Copy last week</Button>
          {week.drafts > 0 && <Button Icon={Send} disabled={busy} onClick={publish}>Publish {week.drafts} draft{week.drafts === 1 ? '' : 's'}</Button>}
        </>} />
      <div className="vsh-bar">
        <Button variant="quiet" Icon={ChevronLeft} aria-label="The week before" onClick={() => setDay(shiftDay(week.week_start, -7))} />
        <strong>Week of {dayLabel(week.week_start)}</strong>
        <Button variant="quiet" Icon={ChevronRight} aria-label="The week after" onClick={() => setDay(shiftDay(week.week_start, 7))} />
      </div>
      {cover.length > 0 && (
        <Card className="vsh-cover" role="status">
          <strong><AlertTriangle size={16} aria-hidden="true" /> {cover.length} shift{cover.length === 1 ? ' needs' : 's need'} cover</strong>
          {cover.map(s => (
            <div key={s.id} className="vsh-shift">
              <span className="vsh-main"><strong>{s.name} · {s.when}</strong><span className="vsh-sub">{s.status === 'sick' ? 'Off sick' : 'Can’t make it'}{s.reason ? `: ${s.reason}` : ''}</span></span>
              <span className="vsh-actions">
                <Button size="sm" onClick={() => setEdit({ date: s.date, shift: s })}>Give to someone</Button>
                <Button size="sm" variant="secondary" Icon={Megaphone} disabled={busy} onClick={() => run(() => api(`${base}/${s.id}/offer`, { method: 'POST', body: {} }), 'Offered to the team')}>Offer to the team</Button>
              </span>
            </div>
          ))}
        </Card>
      )}
      {week.days.map(d => {
        const list = week.shifts.filter(s => s.date === d)
        return (
          <Card key={d} className="vsh-day">
            <div className="vsh-dayhead"><h3>{dayLabel(d)}</h3><Button size="sm" variant="quiet" Icon={Plus} onClick={() => setEdit({ date: d })}>Add</Button></div>
            {list.length === 0 ? <p className="vsh-sub">Nobody yet.</p> : list.map(s => {
              const [tone, label] = STATE[s.status] || STATE.draft
              return (
                <div key={s.id} className="vsh-shift">
                  <span className="vsh-time vula-money">{hhmm(s.when)}</span>
                  <span className="vsh-main"><strong>{s.open ? 'Open shift' : s.name}{s.section ? ` · ${s.section}` : ''}</strong>
                    <span className="vsh-sub">{s.hours} h{s.note ? ` · ${s.note}` : ''}{s.warning ? ` · ${s.name} ${s.warning}` : ''}</span></span>
                  <StatusChip tone={s.open && s.status === 'published' ? 'warn' : tone}>{s.open && s.status === 'published' ? 'Offered to the team' : label}</StatusChip>
                  <span className="vsh-actions">
                    <Button size="sm" variant="secondary" onClick={() => setEdit({ date: d, shift: s })}>Change</Button>
                    <Button size="sm" variant="quiet" Icon={Trash2} aria-label={`Remove the shift at ${hhmm(s.when)}`} disabled={busy}
                      onClick={async () => { if (s.status === 'draft' || await confirmDialog('Cancel this shift? The person is told.')) run(() => api(`${base}/${s.id}`, { method: 'DELETE' })) }} />
                  </span>
                </div>
              )
            })}
          </Card>
        )
      })}
      <Card className="vsh-day">
        <h3>The team this week</h3>
        {week.team.map(m => (
          <div key={m.id} className="vsh-shift">
            <span className="vsh-main"><strong>{m.name}</strong>
              <span className="vsh-sub">{m.availability.weekly.length ? m.availability.weekly.map(r => `${DAYS[r.weekday]} ${r.start}–${r.end}`).join(', ') : 'Hasn’t said when they can work'}
                {m.availability.off.length ? ` · away ${m.availability.off.map(r => (r.from === r.to ? r.from : `${r.from} to ${r.to}`)).join(', ')}` : ''}</span></span>
            <span className="vsh-hours"><span className="vula-figure">{m.hours}</span> h · {m.shifts} shift{m.shifts === 1 ? '' : 's'}</span>
          </div>
        ))}
      </Card>
      {edit && <ShiftSheet tenantId={tenantId} week={week} edit={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); load() }} />}
    </div>
  )
}
