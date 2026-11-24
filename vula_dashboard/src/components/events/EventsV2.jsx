/**
 * EventsV2.jsx — Sell › Events (24 Nov 2026): put on an event, sell tickets on its own page, check
 * people in at the door. A ticket sale is an ordinary order, so the money shows in Orders and the
 * books like any other sale. Refunds are made on the order; that cancels its tickets.
 */
import { useState } from 'react'
import { ArrowLeft, Camera, Check, Copy, ExternalLink, Plus, RefreshCw, Ticket, Trash2, Undo2, X } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, EmptyState, ErrorCard, Field, Input, PageHeader, Skeleton, StatusChip, Textarea, confirmDialog, toast } from '../ui/index.jsx'
import { useBarcodeCamera } from '../stock/useStock'
import { isoToLocal, localToIso, typesBody, useDoor, useEvents, whenText } from './useEvents'
import './events.css'

const money = (c) => formatMoney(c, { cents: 'always' })
const STATE = { draft: ['muted', 'Draft'], on_sale: ['ok', 'On sale'], closed: ['warn', 'Sales closed'], cancelled: ['danger', 'Cancelled'] }
const stateOf = (ev) => (ev.status === 'on_sale' && !ev.selling ? ['warn', 'Sales ended'] : STATE[ev.status] || STATE.draft)
const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast('Copied'), () => toast("Couldn't copy. Select and copy it by hand.", 'danger'))
const blankType = () => ({ key: Math.random().toString(36).slice(2), name: '', price: '', capacity: '', max_per_order: '10' })

function Editor({ ev, x, onDone }) {
  const [f, setF] = useState({
    title: ev?.title || '', starts: isoToLocal(ev?.starts_at), ends: isoToLocal(ev?.ends_at), venue: ev?.venue || '',
    address: ev?.address || '', description: ev?.description || '', image_url: ev?.image_url || '',
    remind: ev ? ev.remind_buyers !== false : true, thank: ev ? ev.thank_buyers !== false : true,
  })
  const [rows, setRows] = useState(ev?.types?.length
    ? ev.types.map(t => ({ key: t.id, id: t.id, name: t.name, price: (t.price_cents / 100).toFixed(2), capacity: String(t.capacity), max_per_order: String(t.max_per_order), sold: t.sold }))
    : [blankType()])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const setRow = (key, k, v) => setRows(rows.map(r => r.key === key ? { ...r, [k]: v } : r))

  const save = async (e) => {
    e.preventDefault()
    const t = typesBody(rows)
    if (t.error) return setError(t.error)
    if (!localToIso(f.starts)) return setError('Say when the event starts.')
    setBusy(true); setError('')
    try {
      const saved = await x.save(ev?.id, { title: f.title, starts_at: localToIso(f.starts), ends_at: localToIso(f.ends) || null,
        venue: f.venue, address: f.address, description: f.description, image_url: f.image_url,
        remind_buyers: f.remind, thank_buyers: f.thank, types: t.types })
      toast('Saved'); onDone(saved)
    } catch (err) { setError(err.message) }
    setBusy(false)
  }

  return (
    <form className="vev" onSubmit={save}>
      <div className="vev-row"><Button type="button" variant="quiet" Icon={ArrowLeft} onClick={() => onDone(null)}>Back</Button></div>
      <Card className="vev-card">
        <Field label="Event name"><Input required maxLength={120} value={f.title} onChange={set('title')} placeholder="Summer night market" /></Field>
        <div className="vev-two">
          <Field label="Starts"><Input required type="datetime-local" value={f.starts} onChange={set('starts')} /></Field>
          <Field label="Ends (optional)"><Input type="datetime-local" value={f.ends} onChange={set('ends')} /></Field>
        </div>
        <div className="vev-two">
          <Field label="Venue"><Input maxLength={120} value={f.venue} onChange={set('venue')} placeholder="The Yard" /></Field>
          <Field label="Address"><Input maxLength={200} value={f.address} onChange={set('address')} placeholder="12 Harbour Road, Hout Bay" /></Field>
        </div>
        <Field label="About the event"><Textarea maxLength={4000} value={f.description} onChange={set('description')} /></Field>
        <Field label="Picture (optional)" hint="The web address of a picture, starting with https://"><Input value={f.image_url} onChange={set('image_url')} placeholder="https://…" /></Field>
      </Card>
      <Card className="vev-card">
        <h3 className="vev-h">Tickets</h3>
        <p className="vev-sub">Each kind of ticket has its own price and number. Buyers pay through your payment gateway.</p>
        <ul className="vev-types">
          {rows.map(r => (
            <li key={r.key} className="vev-type">
              <Field label="Name"><Input value={r.name} maxLength={60} placeholder="General" onChange={e => setRow(r.key, 'name', e.target.value)} /></Field>
              <Field label="Price (R)"><Input value={r.price} inputMode="decimal" placeholder="150.00" onChange={e => setRow(r.key, 'price', e.target.value)} /></Field>
              <Field label="How many" hint={r.sold ? `${r.sold} sold` : undefined}><Input value={r.capacity} inputMode="numeric" placeholder="100" onChange={e => setRow(r.key, 'capacity', e.target.value)} /></Field>
              <Field label="Most per order"><Input value={r.max_per_order} inputMode="numeric" onChange={e => setRow(r.key, 'max_per_order', e.target.value)} /></Field>
              <Button type="button" variant="quiet" size="sm" Icon={Trash2} aria-label={`Remove ${r.name || 'this ticket type'}`} onClick={() => setRows(rows.filter(o => o.key !== r.key))}>Remove</Button>
            </li>
          ))}
        </ul>
        <div className="vev-row"><Button type="button" variant="secondary" size="sm" Icon={Plus} onClick={() => setRows([...rows, blankType()])}>Add a ticket type</Button></div>
      </Card>
      <Card className="vev-card">
        <h3 className="vev-h">WhatsApp messages to buyers</h3>
        <label className="vev-check"><input type="checkbox" checked={f.remind} onChange={e => setF({ ...f, remind: e.target.checked })} />
          <span>Remind buyers the day before, with their tickets<span className="vev-sub">One message to each buyer, with the date, the place and a button to their tickets.</span></span></label>
        <label className="vev-check"><input type="checkbox" checked={f.thank} onChange={e => setF({ ...f, thank: e.target.checked })} />
          <span>Thank the people who came, the day after<span className="vev-sub">Goes only to buyers whose ticket was checked in, with your next event if one is on sale. Never to anyone who opted out.</span></span></label>
      </Card>
      {error && <p className="vev-error" role="alert">{error}</p>}
      <div className="vev-row"><Button type="submit" disabled={busy}>{busy ? 'Saving…' : ev ? 'Save changes' : 'Save as a draft'}</Button></div>
    </form>
  )
}

function Door({ tenantId, ev, onBack }) {
  const d = useDoor(tenantId, ev.id)
  const [code, setCode] = useState('')
  const [last, setLast] = useState(null)
  const [q, setQ] = useState('')
  const tone = { ok: 'ok', already: 'warn', void: 'danger', wrong_event: 'danger', unknown: 'danger' }

  const scan = async (raw) => {
    if (!String(raw || '').trim()) return
    try { setLast(await d.checkIn(String(raw).trim())) } catch (e) { setLast({ result: 'unknown', message: e.message }) }
    setCode('')
  }
  const cam = useBarcodeCamera({ onCode: scan })
  const undo = async (k) => {
    if (!await confirmDialog(`Undo the check-in for ${k.holder_name || 'this ticket'}?`)) return
    try { await d.undo(k.id); toast('Check-in undone') } catch (e) { toast(e.message, 'danger') }
  }

  const info = d.data?.event
  const list = (d.data?.attendees || []).filter(k => k.status !== 'void')
    .filter(k => !q.trim() || `${k.holder_name} ${k.order} ${k.type} ${k.holder_phone}`.toLowerCase().includes(q.trim().toLowerCase()))
  return (
    <div className="vev">
      <div className="vev-row">
        <Button variant="quiet" Icon={ArrowLeft} onClick={onBack}>Back</Button>
        <Button variant="quiet" size="sm" Icon={RefreshCw} onClick={d.load}>Refresh</Button>
      </div>
      <PageHeader title={ev.title} sub={whenText(ev.starts_at)} />
      {d.error && <ErrorCard what="the door list" onRetry={d.load} />}
      {info && (
        <div className="vev-stats">
          <Card><span className="vev-sub">Checked in</span><span className="vula-figure vev-fig">{info.checked_in} of {info.sold}</span></Card>
          <Card><span className="vev-sub">Tickets sold</span><span className="vula-figure vev-fig">{info.sold} of {info.capacity}</span></Card>
          <Card><span className="vev-sub">Ticket takings</span><span className="vula-figure vev-fig">{money(info.takings_cents)}</span></Card>
        </div>
      )}
      <Card className="vev-card">
        <h3 className="vev-h">Check in</h3>
        <form className="vev-scan" onSubmit={e => { e.preventDefault(); scan(code) }}>
          <Input value={code} onChange={e => setCode(e.target.value)} placeholder="Scan or type a ticket code" aria-label="Ticket code" autoComplete="off" />
          <Button type="submit" disabled={!code.trim()}>Check in</Button>
          <Button type="button" variant={cam.on ? 'primary' : 'secondary'} Icon={cam.on ? X : Camera} onClick={() => cam.setOn(!cam.on)}>{cam.on ? 'Stop camera' : 'Use camera'}</Button>
        </form>
        {cam.err && <p className="vev-error" role="alert">{cam.err}</p>}
        <div className="vev-video" hidden={!cam.on}><video ref={cam.videoRef} muted playsInline /></div>
        {last && (
          <div className={`vev-result vev-result--${tone[last.result] || 'danger'}`} role="status" aria-live="assertive">
            <strong>{last.result === 'ok' ? <><Check size={18} aria-hidden="true" /> {last.message}</> : last.message}</strong>
            {last.ticket && <span>{last.ticket.holder_name} · {last.ticket.type}{last.result === 'already' && last.ticket.checked_in_at ? ` · used ${whenText(last.ticket.checked_in_at)}${last.ticket.checked_in_by ? ` by ${last.ticket.checked_in_by}` : ''}` : ''}</span>}
          </div>
        )}
      </Card>
      <Card className="vev-card">
        <h3 className="vev-h">Who's coming</h3>
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Search a name, order or number" aria-label="Search the door list" />
        {!d.data ? <Skeleton lines={4} /> : list.length === 0 ? <p className="vev-sub">{d.data.attendees.length ? 'Nobody matches that.' : 'No tickets sold yet.'}</p> : (
          <ul className="vev-list">
            {list.map(k => (
              <li key={k.id} className="vev-att">
                <span className="vev-att-main"><strong>{k.holder_name || 'No name'}</strong><span className="vev-sub">{k.type} · {k.order}</span></span>
                {k.status === 'checked_in'
                  ? <><StatusChip tone="ok"><Check size={14} aria-hidden="true" />In</StatusChip><Button size="sm" variant="quiet" Icon={Undo2} onClick={() => undo(k)}>Undo</Button></>
                  : <StatusChip tone="muted">Not yet</StatusChip>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

/** The door link (owner or manager): people hired for the night open it on their own phones — no
 *  login — and can only see who is coming to this event and let tickets in. */
function DoorLink({ ev, x }) {
  const [link, setLink] = useState(null)
  const [busy, setBusy] = useState(false)
  const run = async (body, done) => {
    setBusy(true)
    try { const d = await x.doorLink(ev.id, body); setLink(d); if (done) toast(done) } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  if (!link) return <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(undefined)}>Door link for staff</Button>
  if (!link.on) return (
    <div className="vev-door">
      <span className="vev-sub">A door link lets the people on the door check tickets in on their own phones, with no login. It works without signal and stops 12 hours after the event.</span>
      <div className="vev-row"><Button size="sm" disabled={busy} onClick={() => run({}, 'Door link made')}>Make a door link</Button></div>
    </div>
  )
  return (
    <div className="vev-door">
      <span className="vev-sub">Send this only to the people working the door. Anyone with it can let tickets in for this event.</span>
      <code className="vev-code">{link.url}</code>
      <div className="vev-row">
        <Button size="sm" Icon={Copy} onClick={() => copy(link.url)}>Copy</Button>
        <a className="vev-link" href={`https://wa.me/?text=${encodeURIComponent(`Door link for ${ev.title}: ${link.url}`)}`} target="_blank" rel="noreferrer">Send on WhatsApp<ExternalLink size={14} aria-hidden="true" /></a>
        <Button size="sm" variant="quiet" disabled={busy} onClick={async () => { if (await confirmDialog('Make a new door link? The old one stops working on every phone.')) run({ new: true }, 'New door link made') }}>New link</Button>
        <Button size="sm" variant="quiet" disabled={busy} onClick={async () => { if (await confirmDialog('Stop the door link? Phones using it can no longer check tickets in.')) run({ on: false }, 'Door link stopped') }}>Stop</Button>
      </div>
    </div>
  )
}

function EventCard({ ev, x, onEdit, onDoor }) {
  const [tone, label] = stateOf(ev)
  const [busy, setBusy] = useState(false)
  const go = async (status, ask) => {
    if (ask && !await confirmDialog(ask)) return
    setBusy(true)
    try { await x.setStatus(ev.id, status); toast('Done') } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  return (
    <Card className="vev-card">
      <div className="vev-row vev-row--top">
        <div className="vev-main">
          <strong className="vev-title">{ev.title}</strong>
          <span className="vev-sub">{whenText(ev.starts_at)}{ev.venue ? ` · ${ev.venue}` : ''}</span>
        </div>
        <StatusChip tone={tone}>{label}</StatusChip>
      </div>
      <ul className="vev-typelist">
        {ev.types.map(t => (
          <li key={t.id}><span>{t.name} · <span className="vula-money">{money(t.price_cents)}</span></span>
            <span className="vev-sub">{t.sold} sold · {t.left} left{t.held ? ` · ${t.held} being paid for` : ''}</span></li>
        ))}
        {!ev.types.length && <li className="vev-sub">No ticket types yet.</li>}
      </ul>
      <div className="vev-row">
        {ev.status === 'draft' && <Button size="sm" disabled={busy} onClick={() => go('on_sale')}>Put on sale</Button>}
        {ev.status === 'on_sale' && <Button size="sm" variant="secondary" disabled={busy} onClick={() => go('closed', 'Stop selling tickets for this event? Tickets already sold stay valid.')}>Close sales</Button>}
        {ev.status === 'closed' && <Button size="sm" variant="secondary" disabled={busy} onClick={() => go('on_sale')}>Reopen sales</Button>}
        {ev.status !== 'draft' && ev.status !== 'cancelled' && <Button size="sm" Icon={Ticket} onClick={() => onDoor(ev)}>Door list</Button>}
        {ev.status !== 'cancelled' && <Button size="sm" variant="secondary" onClick={() => onEdit(ev)}>Edit</Button>}
        {ev.status !== 'draft' && ev.url && <>
          <Button size="sm" variant="secondary" Icon={Copy} onClick={() => copy(ev.url)}>Copy link</Button>
          <a className="vev-link" href={ev.url} target="_blank" rel="noreferrer">Open page<ExternalLink size={14} aria-hidden="true" /></a>
        </>}
        {ev.status !== 'cancelled' && <Button size="sm" variant="quiet" disabled={busy} onClick={() => go('cancelled', ev.sold
          ? `Cancel this event? ${ev.sold} ticket${ev.sold === 1 ? ' has' : 's have'} been sold. Cancelling stops sales but refunds nobody: refund each order in Orders.`
          : 'Cancel this event? This can’t be undone.')}>Cancel event</Button>}
      </div>
      {ev.status !== 'draft' && ev.status !== 'cancelled' && <DoorLink ev={ev} x={x} />}
    </Card>
  )
}

export default function EventsV2({ tenantId }) {
  const x = useEvents(tenantId)
  const [editing, setEditing] = useState(null)       // null | 'new' | event
  const [door, setDoor] = useState(null)

  if (door) return <Door tenantId={tenantId} ev={door} onBack={() => { setDoor(null); x.load() }} />
  if (editing) return <Editor ev={editing === 'new' ? null : editing} x={x} onDone={() => setEditing(null)} />
  return (
    <div className="vev">
      <PageHeader sub="Put on an event, sell tickets on its own page and check people in at the door. Ticket sales show in Orders and your books like any other sale."
        actions={<Button Icon={Plus} onClick={() => setEditing('new')}>New event</Button>} />
      {x.error ? <ErrorCard what="your events" onRetry={x.load} />
        : !x.events ? <Card><Skeleton lines={4} /></Card>
        : x.events.length === 0 ? <EmptyState Icon={Ticket} title="No events yet">Create an event, add its tickets, then put it on sale to get a page you can share.</EmptyState>
        : x.events.map(ev => <EventCard key={ev.id} ev={ev} x={x} onEdit={setEditing} onDoor={setDoor} />)}
    </div>
  )
}
