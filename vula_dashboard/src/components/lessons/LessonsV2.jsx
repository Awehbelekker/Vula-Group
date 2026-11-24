/**
 * LessonsV2.jsx — Sell › Lessons (24 Nov 2026): lesson and gear-hire requests from WhatsApp and the
 * ones taken by phone, confirmed by a person with a time and a coach; the forecast for the spot;
 * the lessons, gear and prices. Backend: vula_mind/vula/api/lessons.py. Every amount shown is the
 * server's; a price typed here is read as written (lib/money.randToCents), never worked out.
 */
import { useCallback, useEffect, useState } from 'react'
import { Camera, Check, Copy, ExternalLink, Plus, Settings, Trash2, Waves, X } from 'lucide-react'
import { api } from '../../lib/api'
import { formatMoney, randToCents } from '../../lib/money'
import { Button, Card, EmptyState, ErrorCard, Field, Input, PageHeader, PillTabs, Select, Sheet, Skeleton, StatusChip, Textarea, confirmDialog, promptDialog, toast } from '../ui/index.jsx'
import '../tables/tables.css'
import './lessons.css'

const STATE = { requested: ['warn', 'To confirm'], confirmed: ['ok', 'Confirmed'], completed: ['muted', 'Done'], declined: ['danger', 'Declined'], no_show: ['danger', 'No-show'], cancelled: ['danger', 'Cancelled'] }
const DURS = ['2h', '4h', '1d', '2d', '3d']
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const rand = (cents) => cents === null || cents === undefined ? '' : String(cents / 100)
const cents = (text) => String(text ?? '').trim() === '' ? null : randToCents(text)
// the customer app (src/flow/): a page customers can add to their phone, at /flow/<business>
const appLink = (tenantId) => `${window.location.origin}/flow/${String(tenantId).replace(/_/g, '-')}`
/** "70" or "27,5" → hundredths of a percent (7000, 2750), read as written; null when empty, undefined when unreadable. */
const percentToBp = (text) => {
  const t = String(text ?? '').replace(/[%\s]/g, '')
  if (!t) return null
  const m = t.match(/^(\d{1,3})(?:[.,](\d{1,2}))?$/)
  if (!m) return undefined
  const bp = Number(m[1]) * 100 + Number((m[2] || '0').padEnd(2, '0'))
  return bp <= 10000 ? bp : undefined
}
const bpToPercent = (bp) => bp === null || bp === undefined ? '' : (bp % 100 ? `${Math.floor(bp / 100)}.${String(bp % 100).padStart(2, '0').replace(/0$/, '')}` : String(bp / 100))
const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}` }

/** Each coach's month: lessons done, what they were charged at, and the coach's share where one is
 * set. The owner or a manager sees everyone; a coach sees only their own. Figures are the server's. */
function Coaches({ tenantId }) {
  const [month, setMonth] = useState(thisMonth())
  const [all, setAll] = useState(null)
  const [own, setOwn] = useState(null)
  const [error, setError] = useState('')
  const base = `/v1/commerce/${tenantId}/admin/lessons`
  useEffect(() => {
    let live = true
    setAll(null); setOwn(null); setError('')
    api(`${base}/coaches?month=${month}`).then(d => live && setAll(d))
      .catch(e => (e.status === 403 ? api(`${base}/mine?month=${month}`).then(d => live && setOwn(d)) : Promise.reject(e)))
      .catch(e => live && setError(e.message))
    return () => { live = false }
  }, [base, month])
  const pick = <Field label="Month"><Input type="month" value={month} onChange={e => e.target.value && setMonth(e.target.value)} /></Field>
  if (error) return <Card><p className="vtb-error" role="alert">{error}</p></Card>
  if (!all && !own) return <Card><Skeleton lines={3} /></Card>
  if (own) return (
    <Card className="vtb-list">
      <div className="vtb-row">{pick}</div>
      {!own.me ? <p className="vtb-sub">Your login isn't linked to a team member, so there are no lessons under your name.</p> : (<>
        <p className="vtb-sub"><strong>{own.me.name}</strong> — {own.month ? `${own.month.done} done, ${own.month.to_come} to come this month` : 'no lessons this month'}
          {own.month && own.month.share_cents !== null ? `. Your share so far: ${formatMoney(own.month.share_cents)}` : ''}.</p>
        <ul>{own.upcoming.map(r => <li key={r.id} className="vtb-b"><span className="vtb-main"><strong>{r.when}</strong><span className="vtb-sub">{r.customer_name} · {r.summary}{r.level_text ? ` · ${r.level_text}` : ''}</span></span></li>)}</ul>
        {own.upcoming.length === 0 && <p className="vtb-sub">Nothing confirmed for you yet.</p>}
      </>)}
    </Card>
  )
  return (
    <Card className="vtb-list">
      <div className="vtb-row">{pick}</div>
      {all.coaches.length === 0 ? <p className="vtb-sub">No lessons in this month yet.</p> : (
        <ul>
          {all.coaches.map(c => (
            <li key={c.coach_id || 'none'} className="vtb-b">
              <span className="vtb-main"><strong>{c.name}</strong><span className="vtb-sub">{c.done} done · {c.people} people · {c.to_come} to come</span></span>
              <span className="vls-amount vula-money">{formatMoney(c.value_cents)}</span>
              {c.share_cents !== null && c.coach_id && <StatusChip tone="ok">Share {formatMoney(c.share_cents)}</StatusChip>}
            </li>))}
          <li className="vtb-b"><span className="vtb-main"><strong>Total</strong><span className="vtb-sub">{all.total.done} lessons done</span></span>
            <span className="vls-amount vula-money">{formatMoney(all.total.value_cents)}</span>
            {all.total.share_cents !== null && <StatusChip tone="ok">Shares {formatMoney(all.total.share_cents)}</StatusChip>}</li>
        </ul>)}
      <p className="vtb-sub">{all.coach_share_bp === null ? 'Set the coach’s share in Lessons and prices to see what each coach has earned.' : `A coach’s share is ${bpToPercent(all.coach_share_bp)}% of each lesson marked done. This is a report: nothing is paid out from here.`}</p>
    </Card>
  )
}

const PART_TIME = { morning: '09:00', midday: '12:00', afternoon: '15:00', any: '09:00' }

/** What the water looks like now: a team member's photo from the beach, read into a few fixed
 * words by the server. The photo is sent once and not kept; only the reading is. */
function BeachReport({ tenantId }) {
  const [report, setReport] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [points, setPoints] = useState(null)
  const base = `/v1/commerce/${tenantId}/admin/lessons/beach-report`
  const loadPoints = useCallback(() => api(`/v1/commerce/${tenantId}/admin/lessons/sharing`).then(setPoints).catch(() => {}), [tenantId])
  useEffect(() => { api(base).then((d) => setReport(d.report)).catch(() => {}); loadPoints() }, [base, loadPoints])
  const send = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setBusy(true); setError('')
    try {
      const fd = new FormData(); fd.append('file', file); fd.append('note', note)
      const d = await api(base, { method: 'POST', body: fd })
      setReport(d.report); setNote(''); toast('Report added. Your customers can see it in the app.'); loadPoints()
    } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <div className="vls-beach">
      <p className="vtb-sub">{report ? <><strong>Seen from the beach at {report.time}{report.by ? ` by ${report.by}` : ''}:</strong> {report.text}{report.coach_note ? `. “${report.coach_note}”` : ''}</> : 'No report from the beach in the last 4 hours.'}</p>
      <div className="vtb-row">
        <Field label="Your note for customers (optional)"><Input value={note} maxLength={160} placeholder="Small and clean, good for first timers" onChange={e => setNote(e.target.value)} /></Field>
        <label className={`vtb-upload${busy ? ' vls-busy' : ''}`}><Camera size={16} aria-hidden="true" />{busy ? 'Reading the photo…' : 'Take a photo of the water'}
          <input type="file" accept="image/*" capture="environment" hidden disabled={busy} onChange={send} /></label>
      </div>
      {error && <p className="vtb-error" role="alert">{error}</p>}
      <p className="vtb-sub">Face the waves. The photo is read once and not kept; customers see only the words, the time and your first name.</p>
      {points && (
        <div className="vls-points">
          <p className="vtb-sub"><strong>Sharing points this month</strong>{points.me ? ` — yours: ${points.me.points}` : ''}</p>
          {points.board.length > 0 && <ul>{points.board.map((c, i) => (
            <li key={c.member_id}><span>{i + 1}. {c.name}</span><span className="vtb-sub">{c.reports} {c.reports === 1 ? 'report' : 'reports'}</span><strong className="vula-money">{c.points}</strong></li>))}</ul>}
          <p className="vtb-sub">{points.rule}</p>
        </div>)}
    </div>
  )
}

function Conditions({ tenantId }) {
  const [day, setDay] = useState(0)
  const [d, setD] = useState(null)
  useEffect(() => {
    const when = new Date(); when.setDate(when.getDate() + day)
    setD(null)
    api(`/v1/commerce/${tenantId}/admin/lessons/conditions?date=${iso(when)}`).then(setD).catch(() => setD({ unavailable: true, hours: [] }))
  }, [tenantId, day])
  if (d && !d.spot && !d.unavailable) return null
  return (
    <Card className="vls-cond">
      <div className="vls-condhead"><strong>Conditions{d?.spot ? ` at ${d.spot.name}` : ''}</strong>
        <PillTabs label="Day" tabs={[{ id: 0, label: 'Today' }, { id: 1, label: 'Tomorrow' }, { id: 2, label: 'Day after' }]} active={day} onChange={setDay} /></div>
      {(d?.cameras || []).map(c => (
        <p key={c.id} className="vtb-sub"><strong>{c.name}</strong> · camera by {c.owner} · <a className="vtb-a" href={c.page_url} target="_blank" rel="noreferrer">Watch live<ExternalLink size={14} aria-hidden="true" /></a>
          {c.kind === 'link' ? ' (opens their page; your customers get the same link)' : ''}</p>))}
      {d?.summary?.headline && <p className="vtb-sub"><strong>{d.summary.headline}</strong> {d.summary.confidence} Your customers see this in the app.</p>}
      {!d ? <Skeleton lines={2} /> : d.unavailable || !d.hours.length ? <p className="vtb-sub">The forecast couldn't be read just now.</p> : (
        <div className="vls-hours">
          {d.hours.map(h => (
            <div key={h.hour} className="vls-hour" data-rating={h.rating} title={[h.text, ...(h.reasons || [])].join(' — ')}>
              <strong>{h.hour}</strong>
              <span>{h.wave_m !== undefined ? `${h.wave_m.toFixed(1)} m` : '—'}{h.period_s !== undefined ? ` · ${Math.round(h.period_s)} s` : ''}</span>
              <span>{h.wind_kn !== undefined ? `${Math.round(h.wind_kn)} kn` : '—'}{h.gust_kn !== undefined ? ` (${Math.round(h.gust_kn)})` : ''}</span>
              <span>{h.rating === 'good' ? 'Good' : h.rating === 'poor' ? 'Poor' : 'Marginal'}</span>
            </div>
          ))}
        </div>
      )}
      <p className="vtb-sub">Swell height and period, wind and gusts in knots, rated for a beginner surf lesson. A forecast for open water: a guide, not a decision.</p>
      <BeachReport tenantId={tenantId} />
    </Card>
  )
}

function ConfirmSheet({ tenantId, r, coaches, onClose, onDone }) {
  const start = r.starts_at ? new Date(r.starts_at) : null
  const [f, setF] = useState({ date: start ? iso(start) : r.wanted_date, time: start ? start.toTimeString().slice(0, 5) : PART_TIME[r.wanted_part] || '09:00',
    coach_id: r.coach_id || '', price: r.amount_cents === null ? '' : rand(r.amount_cents), invoice: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async (e) => {
    e.preventDefault(); setError('')
    const price = cents(f.price)
    if (price === null) { setError('Enter the price in rand.'); return }
    setBusy(true)
    try {
      const body = { starts_at: `${f.date}T${f.time}:00+02:00`, coach_id: f.coach_id, invoice: f.invoice }
      if (price !== r.amount_cents) body.price_cents = price
      const d = await api(`/v1/commerce/${tenantId}/admin/lessons/${r.id}/confirm`, { method: 'POST', body })
      toast(d.request.customer_told ? 'Confirmed. The customer has been told.' : 'Confirmed. The customer could not be messaged — let them know.', d.request.customer_told ? 'ok' : 'danger')
      onDone()
    } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <Sheet open title={`${r.status === 'confirmed' ? 'Change' : 'Confirm'} ${r.ref}`} onClose={onClose}>
      <form className="vtb-form" onSubmit={save}>
        <p className="vtb-sub"><strong>{r.customer_name}</strong> — {r.summary}. Asked for {r.wanted}{r.level_text ? `. ${r.level_text}` : ''}.</p>
        <div className="vtb-row">
          <Field label="Day"><Input type="date" required value={f.date} onChange={e => setF({ ...f, date: e.target.value })} /></Field>
          <Field label="Time"><Input type="time" required value={f.time} onChange={e => setF({ ...f, time: e.target.value })} /></Field>
        </div>
        <Field label={r.kind === 'hire' ? 'Handled by (optional)' : 'Coach'}><Select value={f.coach_id} onChange={e => setF({ ...f, coach_id: e.target.value })}>
          <option value="">Not decided yet</option>{coaches.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
        <Field label="Price (rand)" hint={r.quote_cents === null ? 'Your price list has no price for this. Enter what you agreed.' : `Your price list: ${formatMoney(r.quote_cents)}.`}>
          <Input inputMode="decimal" value={f.price} onChange={e => setF({ ...f, price: e.target.value })} /></Field>
        <label className="vtb-check"><input type="checkbox" checked={f.invoice} onChange={e => setF({ ...f, invoice: e.target.checked })} /><span>Make a draft invoice for it</span></label>
        {error && <p className="vtb-error" role="alert">{error}</p>}
        <div><Button type="submit" Icon={Check} disabled={busy}>{busy ? 'Saving…' : 'Confirm and tell the customer'}</Button></div>
      </form>
    </Sheet>
  )
}

function AddSheet({ tenantId, st, onClose, onDone }) {
  const [f, setF] = useState({ kind: st.lessons.length ? 'lesson' : 'hire', item_id: (st.lessons[0] || st.hire[0])?.id || '', format: 'group', people: 1, package: false,
    duration: '2h', wanted_date: iso(new Date()), wanted_part: 'any', level: '', name: '', phone: '', notes: '' })
  const [q, setQ] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const items = f.kind === 'lesson' ? st.lessons : st.hire
  const wanted = { kind: f.kind, item_id: f.item_id, format: f.format, people: Number(f.people) || 1, package: f.package, duration: f.duration }
  const key = JSON.stringify(wanted)
  useEffect(() => {
    let live = true
    api(`/v1/commerce/${tenantId}/admin/lessons/quote`, { method: 'POST', body: JSON.parse(key) }).then(d => live && setQ(d)).catch(() => live && setQ(null))
    return () => { live = false }
  }, [tenantId, key])
  const save = async (e) => {
    e.preventDefault(); setBusy(true); setError('')
    try { await api(`/v1/commerce/${tenantId}/admin/lessons`, { method: 'POST', body: { ...f, ...wanted } }); toast('Added'); onDone() } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <Sheet open title="Add a request" onClose={onClose}>
      <form className="vtb-form" onSubmit={save}>
        <div className="vtb-row">
          <Field label="What"><Select value={f.kind} onChange={e => { const kind = e.target.value; setF({ ...f, kind, item_id: (kind === 'lesson' ? st.lessons : st.hire)[0]?.id || '' }) }}>
            {st.lessons.length > 0 && <option value="lesson">A lesson</option>}{st.hire.length > 0 && <option value="hire">Gear hire</option>}</Select></Field>
          <Field label={f.kind === 'lesson' ? 'Lesson' : 'Gear'}><Select value={f.item_id} onChange={e => setF({ ...f, item_id: e.target.value })}>{items.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}</Select></Field>
        </div>
        <div className="vtb-row">
          {f.kind === 'lesson' ? (<>
            <Field label="Type"><Select value={f.format} onChange={e => setF({ ...f, format: e.target.value })}><option value="group">Group</option><option value="private">Private</option></Select></Field>
            <Field label="People"><Input inputMode="numeric" value={f.people} onChange={e => setF({ ...f, people: e.target.value })} /></Field>
            <Field label="Sessions"><Select value={f.package ? 'k' : '1'} onChange={e => setF({ ...f, package: e.target.value === 'k' })}><option value="1">One lesson</option><option value="k">A package</option></Select></Field>
          </>) : (<>
            <Field label="For how long"><Select value={f.duration} onChange={e => setF({ ...f, duration: e.target.value })}>{[...DURS, '7d'].map(d => <option key={d} value={d}>{d.replace('h', ' hours').replace('d', d === '1d' ? ' day' : ' days')}</option>)}</Select></Field>
            <Field label="How many"><Input inputMode="numeric" value={f.people} onChange={e => setF({ ...f, people: e.target.value })} /></Field>
          </>)}
        </div>
        <p className="vtb-sub">{q ? (q.quote_cents === null ? 'Your price list has no price for this. Set it when you confirm.' : `Your price list: ${formatMoney(q.quote_cents)}.`) : ' '}</p>
        <div className="vtb-row">
          <Field label="Day wanted"><Input type="date" required value={f.wanted_date} onChange={e => setF({ ...f, wanted_date: e.target.value })} /></Field>
          <Field label="Time of day"><Select value={f.wanted_part} onChange={e => setF({ ...f, wanted_part: e.target.value })}><option value="any">Any time</option><option value="morning">Morning</option><option value="midday">Midday</option><option value="afternoon">Afternoon</option></Select></Field>
        </div>
        <Field label="Name"><Input required value={f.name} maxLength={80} onChange={e => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Cellphone (optional)" hint="With a number, they are told on WhatsApp when you confirm."><Input type="tel" value={f.phone} onChange={e => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="Note (optional)"><Input value={f.notes} maxLength={300} onChange={e => setF({ ...f, notes: e.target.value })} /></Field>
        {error && <p className="vtb-error" role="alert">{error}</p>}
        <div><Button type="submit" disabled={busy || !f.item_id}>{busy ? 'Adding…' : 'Add the request'}</Button></div>
      </form>
    </Sheet>
  )
}

const blankLesson = () => ({ name: '', sport: 'surf', minutes: 90, max_per_coach: 5, package_sessions: 4, group: [{ min: 1, max: '', single: '', pack: '' }], private_single: '', private_pack: '', extras: [] })
const toForm = (st) => ({
  enabled: st.enabled, share: bpToPercent(st.coach_share_bp), form_url: st.form_url || '', pay_note: st.pay_note || '', spot_name: st.spot?.name || '', lat: st.spot?.lat ?? '', lng: st.spot?.lng ?? '', faces: st.spot?.faces ?? '',
  lessons: st.lessons.map(l => ({ id: l.id, name: l.name, sport: l.sport, minutes: l.minutes, max_per_coach: l.max_per_coach, package_sessions: l.package_sessions,
    group: l.group.map(t => ({ min: t.min, max: t.max ?? '', single: rand(t.single_cents), pack: rand(t.package_cents) })),
    private_single: rand(l.private?.single_cents), private_pack: rand(l.private?.package_cents), extras: l.extras.map(e => ({ id: e.id, name: e.name, price: rand(e.cents) })) })),
  hire: st.hire.map(g => ({ id: g.id, name: g.name, extra: rand(g.extra_day_cents), ...Object.fromEntries(DURS.map(d => [d, rand(g.prices[d])])) })),
})
/** The form as the server wants it; throws a sentence when a typed price can't be read. */
function fromForm(f) {
  const money = (text, what) => { const c = cents(text); if (String(text ?? '').trim() !== '' && c === null) throw new Error(`${what}: that price couldn't be read.`); return c }
  const whole = (v) => v === '' || v === null ? null : Number(v)
  const share = percentToBp(f.share)
  if (share === undefined) throw new Error("The coach's share: enter a percentage between 0 and 100.")
  return {
    enabled: f.enabled, coach_share_bp: share, form_url: f.form_url, pay_note: f.pay_note,
    spot: f.spot_name.trim() ? { name: f.spot_name, lat: Number(f.lat), lng: Number(f.lng), faces: f.faces === '' ? '' : Number(f.faces) } : null,
    lessons: f.lessons.map(l => ({ id: l.id, name: l.name, sport: l.sport, minutes: Number(l.minutes), max_per_coach: Number(l.max_per_coach), package_sessions: Number(l.package_sessions),
      group: l.group.filter(t => String(t.single).trim() !== '').map(t => ({ min: Number(t.min), max: whole(t.max), single_cents: money(t.single, l.name), package_cents: money(t.pack, l.name) })),
      private: String(l.private_single).trim() === '' ? null : { single_cents: money(l.private_single, l.name), package_cents: money(l.private_pack, l.name) },
      extras: l.extras.filter(e => e.name.trim()).map(e => ({ id: e.id, name: e.name, cents: money(e.price, e.name) })) })),
    hire: f.hire.map(g => ({ id: g.id, name: g.name, extra_day_cents: money(g.extra, g.name),
      prices: Object.fromEntries(DURS.filter(d => String(g[d] ?? '').trim() !== '').map(d => [d, money(g[d], g.name)])) })),
  }
}

function SettingsSheet({ tenantId, st, onClose, onSaved }) {
  const [f, setF] = useState(() => toForm(st))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const setL = (i, patch) => setF({ ...f, lessons: f.lessons.map((l, n) => n === i ? { ...l, ...patch } : l) })
  const setG = (i, patch) => setF({ ...f, hire: f.hire.map((g, n) => n === i ? { ...g, ...patch } : g) })
  const setT = (i, k, patch) => setL(i, { group: f.lessons[i].group.map((t, n) => n === k ? { ...t, ...patch } : t) })
  const save = async () => {
    setBusy(true); setError('')
    try { const d = await api(`/v1/commerce/${tenantId}/admin/lessons/settings`, { method: 'PUT', body: fromForm(f) }); toast('Saved'); onSaved(d) } catch (e) { setError(e.message) }
    setBusy(false)
  }
  return (
    <Sheet open title="Lessons, gear and prices" onClose={onClose} footer={<Button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>}>
      <div className="vtb-form">
        <label className="vtb-check"><input type="checkbox" checked={!!f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} /><span>Take lesson and hire requests on WhatsApp</span></label>
        <h3 className="vtb-h">Lessons</h3>
        {f.lessons.map((l, i) => (
          <div key={i} className="vls-block">
            <div className="vtb-row">
              <Field label="Name"><Input value={l.name} maxLength={24} placeholder="Surf lesson" onChange={e => setL(i, { name: e.target.value })} /></Field>
              <Field label="Sport"><Select value={l.sport} onChange={e => setL(i, { sport: e.target.value })}><option value="surf">Surf</option><option value="sup">SUP</option><option value="kite">Kite</option><option value="skate">Skate</option><option value="other">Other</option></Select></Field>
              <Field label="People per coach"><Input inputMode="numeric" value={l.max_per_coach} onChange={e => setL(i, { max_per_coach: e.target.value })} /></Field>
              <Field label="Lessons in a package"><Input inputMode="numeric" value={l.package_sessions} onChange={e => setL(i, { package_sessions: e.target.value })} /></Field>
            </div>
            <p className="vtb-sub">Group prices, per person, by how many come. Leave “up to” empty for “or more”.</p>
            {l.group.map((t, k) => (
              <div key={k} className="vls-tier">
                <Field label="From (people)"><Input inputMode="numeric" value={t.min} onChange={e => setT(i, k, { min: e.target.value })} /></Field>
                <Field label="Up to"><Input inputMode="numeric" value={t.max} onChange={e => setT(i, k, { max: e.target.value })} /></Field>
                <Field label="One lesson (R)"><Input inputMode="decimal" value={t.single} onChange={e => setT(i, k, { single: e.target.value })} /></Field>
                <Field label="Package (R)"><Input inputMode="decimal" value={t.pack} onChange={e => setT(i, k, { pack: e.target.value })} /></Field>
              </div>
            ))}
            <div className="vtb-actions"><Button type="button" size="sm" variant="quiet" Icon={Plus} onClick={() => setL(i, { group: [...l.group, { min: '', max: '', single: '', pack: '' }] })}>Add a group price</Button></div>
            <div className="vtb-row">
              <Field label="Private 1-on-1 (R)"><Input inputMode="decimal" value={l.private_single} onChange={e => setL(i, { private_single: e.target.value })} /></Field>
              <Field label="Private package (R)"><Input inputMode="decimal" value={l.private_pack} onChange={e => setL(i, { private_pack: e.target.value })} /></Field>
              <Field label="Extra (like Wetsuit)"><Input value={l.extras[0]?.name || ''} maxLength={20} onChange={e => setL(i, { extras: [{ ...(l.extras[0] || { price: '' }), name: e.target.value }] })} /></Field>
              <Field label="Extra, per person per lesson (R)"><Input inputMode="decimal" value={l.extras[0]?.price || ''} onChange={e => setL(i, { extras: [{ ...(l.extras[0] || { name: '' }), price: e.target.value }] })} /></Field>
            </div>
            <div><Button type="button" size="sm" variant="quiet" Icon={Trash2} onClick={() => setF({ ...f, lessons: f.lessons.filter((_, n) => n !== i) })}>Remove this lesson</Button></div>
          </div>
        ))}
        <div><Button type="button" size="sm" variant="secondary" Icon={Plus} onClick={() => setF({ ...f, lessons: [...f.lessons, blankLesson()] })}>Add a lesson</Button></div>
        <h3 className="vtb-h">Gear for hire</h3>
        {f.hire.map((g, i) => (
          <div key={i} className="vls-block">
            <div className="vls-tier">
              <Field label="Gear"><Input value={g.name} maxLength={24} placeholder="Surfboard" onChange={e => setG(i, { name: e.target.value })} /></Field>
              {DURS.map(d => <Field key={d} label={`${d.replace('h', ' hours').replace('d', d === '1d' ? ' day' : ' days')} (R)`}><Input inputMode="decimal" value={g[d] ?? ''} onChange={e => setG(i, { [d]: e.target.value })} /></Field>)}
              <Field label="Each day from 4 days (R)"><Input inputMode="decimal" value={g.extra} onChange={e => setG(i, { extra: e.target.value })} /></Field>
            </div>
            <div><Button type="button" size="sm" variant="quiet" Icon={Trash2} onClick={() => setF({ ...f, hire: f.hire.filter((_, n) => n !== i) })}>Remove</Button></div>
          </div>
        ))}
        <div><Button type="button" size="sm" variant="secondary" Icon={Plus} onClick={() => setF({ ...f, hire: [...f.hire, { name: '', extra: '' }] })}>Add gear</Button></div>
        <h3 className="vtb-h">Where lessons run</h3>
        <div className="vtb-row">
          <Field label="Spot" hint="With its position, you get tomorrow's forecast for each lesson."><Input value={f.spot_name} maxLength={60} placeholder="Big Bay" onChange={e => setF({ ...f, spot_name: e.target.value })} /></Field>
          <Field label="Latitude"><Input inputMode="decimal" value={f.lat} placeholder="-33.794" onChange={e => setF({ ...f, lat: e.target.value })} /></Field>
          <Field label="Longitude"><Input inputMode="decimal" value={f.lng} placeholder="18.459" onChange={e => setF({ ...f, lng: e.target.value })} /></Field>
        </div>
        <Field label="The beach faces" hint="Standing on the sand looking out to sea, which way are you looking? The forecast then says when the wind is onshore or offshore.">
          <Select value={f.faces} onChange={e => setF({ ...f, faces: e.target.value })}>
            <option value="">Not set</option>
            {['North', 'North-east', 'East', 'South-east', 'South', 'South-west', 'West', 'North-west'].map((n, i) => <option key={n} value={i * 45}>{n}</option>)}
          </Select></Field>
        <Field label="Indemnity form (web address, optional)" hint="Sent with every confirmation."><Input type="url" value={f.form_url} placeholder="https://" onChange={e => setF({ ...f, form_url: e.target.value })} /></Field>
        <Field label="Coach's share of each lesson (%, optional)" hint="Used only for the Coaches report. Nothing is paid out by Vula."><Input inputMode="decimal" value={f.share} placeholder="70" onChange={e => setF({ ...f, share: e.target.value })} /></Field>
        <Field label="How to pay (optional)" hint="Added to the confirmation, in your words."><Textarea rows={2} maxLength={300} value={f.pay_note} placeholder="Pay at the shop or by EFT before your lesson." onChange={e => setF({ ...f, pay_note: e.target.value })} /></Field>
        {error && <p className="vtb-error" role="alert">{error}</p>}
      </div>
    </Sheet>
  )
}

const SPORT_NAMES = { surf: 'Surf', sup: 'SUP', kite: 'Kite', skate: 'Skate', other: 'Other' }

/** What customers see of each coach, and whether the school can be found in the customer app's
 * "find a school" list. Both are the school's choice; a coach's name is their first name from the
 * team list. */
function DirectorySheet({ tenantId, conf, team, onClose, onSaved }) {
  const [listing, setListing] = useState(() => ({ listed: !!conf.directory.listed, about: conf.directory.about || '', town: conf.directory.town || '' }))
  const [profiles, setProfiles] = useState(() => team.map(m => {
    const p = (conf.settings.coach_profiles || []).find(x => x.member_id === m.id) || {}
    return { member_id: m.id, name: m.name, bio: p.bio || '', photo_url: p.photo_url || '', levels: p.levels || [], sports: p.sports || [], shown: !!p.shown }
  }))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const setP = (i, patch) => setProfiles(profiles.map((p, n) => n === i ? { ...p, ...patch } : p))
  const flip = (list, k) => (list.includes(k) ? list.filter(x => x !== k) : [...list, k])
  const photo = async (i, e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setBusy(true); setError('')
    const { uploadBrandImage } = await import('../../lib/brandUpload')   // only when a photo is added
    const url = await uploadBrandImage(tenantId, file, 'coaches')
    if (url) setP(i, { photo_url: url }); else setError("The photo didn't upload. Try a smaller one.")
    setBusy(false)
  }
  const save = async () => {
    setBusy(true); setError('')
    try {
      const body = { listing, coach_profiles: profiles.filter(p => p.shown || p.bio || p.photo_url || p.levels.length || p.sports.length).map(({ name: _n, ...p }) => ({ ...p, photo_url: p.photo_url || null })) }
      const d = await api(`/v1/commerce/${tenantId}/admin/lessons/settings`, { method: 'PUT', body })
      toast(d.directory.listed ? (d.directory.showing ? 'Saved. Your school is in the list.' : `Saved. Not showing yet: ${d.directory.why_not}`) : 'Saved'); onSaved(d)
    } catch (e) { setError(e.message) }
    setBusy(false)
  }
  return (
    <Sheet open title="Coaches and being found" onClose={onClose} footer={<Button onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>}>
      <div className="vtb-form">
        <h3 className="vtb-h">Find a school</h3>
        <label className="vtb-check"><input type="checkbox" checked={listing.listed} onChange={e => setListing({ ...listing, listed: e.target.checked })} /><span>List my school where customers look for one near them</span></label>
        <p className="vtb-sub">Listed schools appear with their beach on a map, to about 100 m, next to other schools. Your own app link always shows only you.</p>
        <div className="vtb-row">
          <Field label="Town"><Input value={listing.town} maxLength={conf.directory.town_max} placeholder="Table View" onChange={e => setListing({ ...listing, town: e.target.value })} /></Field>
          <Field label="One line about your school"><Input value={listing.about} maxLength={conf.directory.about_max} placeholder="Lessons for all ages, every day the waves allow" onChange={e => setListing({ ...listing, about: e.target.value })} /></Field>
        </div>
        <h3 className="vtb-h">Coach profiles</h3>
        {profiles.length === 0 && <p className="vtb-sub">Add your coaches in Team first. Each one can then have a profile here.</p>}
        {profiles.map((p, i) => (
          <div key={p.member_id} className="vls-block">
            <div className="vls-coach">
              {p.photo_url ? <img src={p.photo_url} alt="" /> : <span className="vls-face" aria-hidden="true">{p.name.slice(0, 1)}</span>}
              <span className="vtb-main"><strong>{p.name.split(' ')[0]}</strong><span className="vtb-sub">Customers see the first name only.</span></span>
              <label className="vtb-upload"><Camera size={16} aria-hidden="true" />{p.photo_url ? 'Change photo' : 'Add a photo'}<input type="file" accept="image/*" hidden disabled={busy} onChange={e => photo(i, e)} /></label>
            </div>
            <label className="vtb-check"><input type="checkbox" checked={p.shown} onChange={e => setP(i, { shown: e.target.checked })} /><span>Show this coach to customers</span></label>
            <Field label="About them"><Textarea rows={2} maxLength={300} value={p.bio} placeholder="Surfing Big Bay since 2009. Patient with first timers." onChange={e => setP(i, { bio: e.target.value })} /></Field>
            <div className="vls-ticks"><span className="vtb-sub">Teaches</span>
              {Object.entries(conf.levels).map(([k, label]) => <label key={k} className="vtb-check"><input type="checkbox" checked={p.levels.includes(k)} onChange={() => setP(i, { levels: flip(p.levels, k) })} /><span>{label.split(' (')[0]}</span></label>)}</div>
            <div className="vls-ticks"><span className="vtb-sub">Sports</span>
              {(conf.sports || []).map(k => <label key={k} className="vtb-check"><input type="checkbox" checked={p.sports.includes(k)} onChange={() => setP(i, { sports: flip(p.sports, k) })} /><span>{SPORT_NAMES[k] || k}</span></label>)}</div>
          </div>
        ))}
        {error && <p className="vtb-error" role="alert">{error}</p>}
      </div>
    </Sheet>
  )
}

export default function LessonsV2({ tenantId }) {
  const [data, setData] = useState(null)
  const [conf, setConf] = useState(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('open')
  const [open, setOpen] = useState(null)          // null | 'settings' | 'add' | {confirm: r}
  const base = `/v1/commerce/${tenantId}/admin/lessons`

  const load = useCallback(() => {
    setError('')
    Promise.all([api(base), api(`${base}/settings`)]).then(([d, c]) => { setData(d); setConf(c) }).catch(e => setError(e.message))
  }, [base])
  useEffect(() => { load() }, [load])

  const mark = async (r, status, ask) => {
    if (ask && !await confirmDialog(ask)) return
    try { await api(`${base}/${r.id}/status`, { method: 'POST', body: { status } }); load() } catch (e) { toast(e.message, 'danger') }
  }
  const decline = async (r) => {
    const reason = await promptDialog(`Why can't you do ${r.customer_name}'s request? They are told this.`)
    if (!reason) return
    try { const d = await api(`${base}/${r.id}/decline`, { method: 'POST', body: { reason } }); toast(d.request.customer_told ? 'Declined. The customer has been told.' : 'Declined. The customer could not be messaged.'); load() } catch (e) { toast(e.message, 'danger') }
  }

  if (error) return <ErrorCard what="your lessons" onRetry={load} />
  if (!data || !conf) return <Card><Skeleton lines={5} /></Card>
  const st = conf.settings
  const isOpen = (r) => r.status === 'requested' || r.status === 'confirmed'
  // what's waiting for an answer first, then what's confirmed, in the server's order (by time)
  const rows = tab === 'open' ? [...data.requests.filter(r => r.status === 'requested'), ...data.requests.filter(r => r.status === 'confirmed')] : data.requests.filter(r => !isOpen(r)).reverse()
  return (
    <div className="vtb">
      <PageHeader sub="Lesson and gear requests from WhatsApp and the ones you take by phone. Nothing is booked until you confirm it."
        actions={<><Button variant="secondary" Icon={Settings} onClick={() => setOpen('settings')}>Lessons and prices</Button>
          {st.enabled && <Button variant="secondary" Icon={Waves} onClick={() => setOpen('directory')}>Coaches and being found</Button>}
          {st.enabled && <Button Icon={Plus} onClick={() => setOpen('add')}>Add a request</Button>}</>} />
      {!st.enabled ? (
        <EmptyState Icon={Waves} title="Lesson requests are off" action={<Button onClick={() => setOpen('settings')}>Set up lessons</Button>}>
          Add your lessons, your gear and their prices. Customers then ask on WhatsApp, get your price, and you confirm the time and the coach.
        </EmptyState>
      ) : (<>
        <Card className="vtb-link">
          <span className="vtb-sub">Your customers' app</span><code>{appLink(tenantId)}</code>
          <span className="vtb-actions"><Button size="sm" variant="secondary" Icon={Copy} onClick={() => navigator.clipboard?.writeText(appLink(tenantId)).then(() => toast('Copied'), () => toast("Couldn't copy.", 'danger'))}>Copy</Button>
            <a className="vtb-a" href={appLink(tenantId)} target="_blank" rel="noreferrer">Open<ExternalLink size={14} aria-hidden="true" /></a></span>
        </Card>
        <Conditions tenantId={tenantId} />
        <PillTabs label="Requests" tabs={[{ id: 'open', label: `Open${data.waiting ? ` · ${data.waiting} to confirm` : ''}` }, { id: 'past', label: 'Earlier' }, { id: 'coaches', label: 'Coaches' }]} active={tab} onChange={setTab} />
        {tab === 'coaches' ? <Coaches tenantId={tenantId} /> : rows.length === 0 ? <Card><p className="vtb-sub">{tab === 'open' ? 'Nothing waiting. A new request shows here and the team is told on WhatsApp.' : 'Nothing earlier yet.'}</p></Card> : (
          <Card className="vtb-list">
            <ul>
              {rows.map(r => {
                const [tone, label] = STATE[r.status] || STATE.requested
                return (
                  <li key={r.id} className={`vtb-b${['cancelled', 'no_show', 'declined'].includes(r.status) ? ' vtb-b--off' : ''}`}>
                    <span className="vtb-main"><strong>{r.customer_name} · {r.summary}</strong>
                      <span className="vtb-sub">{r.when ? `${r.when}${r.coach_name ? ` with ${r.coach_name}` : ''}` : `Asked for ${r.wanted}`}</span>
                      <span className="vtb-sub">{[r.ref, r.level_text, r.customer_phone && `+${r.customer_phone}`, r.notes, r.decline_reason, r.conditions && `Forecast: ${r.conditions.rating}`].filter(Boolean).join(' · ')}</span></span>
                    <span className="vls-amount vula-money">{r.amount_cents === null ? 'No price yet' : formatMoney(r.amount_cents)}</span>
                    <StatusChip tone={tone}>{label}</StatusChip>
                    <span className="vtb-actions">
                      {r.status === 'requested' && <><Button size="sm" Icon={Check} onClick={() => setOpen({ confirm: r })}>Confirm</Button>
                        <Button size="sm" variant="quiet" Icon={X} onClick={() => decline(r)}>Decline</Button></>}
                      {r.status === 'confirmed' && <><Button size="sm" variant="secondary" onClick={() => mark(r, 'completed')}>Done</Button>
                        <Button size="sm" variant="quiet" onClick={() => setOpen({ confirm: r })}>Change</Button>
                        <Button size="sm" variant="quiet" onClick={() => mark(r, 'no_show')}>No-show</Button>
                        <Button size="sm" variant="quiet" onClick={() => mark(r, 'cancelled', `Cancel ${r.customer_name}'s ${r.item_name.toLowerCase()}? Let them know yourself.`)}>Cancel</Button></>}
                    </span>
                  </li>
                )
              })}
            </ul>
          </Card>
        )}
      </>)}
      {open === 'directory' && <DirectorySheet tenantId={tenantId} conf={conf} team={data.coaches || []} onClose={() => setOpen(null)} onSaved={(d) => { setConf(d); setOpen(null) }} />}
      {open === 'settings' && <SettingsSheet tenantId={tenantId} st={st} onClose={() => setOpen(null)} onSaved={(d) => { setConf(d); setOpen(null); load() }} />}
      {open === 'add' && <AddSheet tenantId={tenantId} st={st} onClose={() => setOpen(null)} onDone={() => { setOpen(null); load() }} />}
      {open?.confirm && <ConfirmSheet tenantId={tenantId} r={open.confirm} coaches={data.coaches} onClose={() => setOpen(null)} onDone={() => { setOpen(null); load() }} />}
    </div>
  )
}
