/**
 * DoorPage.jsx — /door/#<secret> (24 Nov 2026): the page the people on the door use. No login: the
 * event's door link is the way in, and it can do two things for that one event — show who is
 * coming and let tickets in. It keeps working with no signal (door/useDoor.js).
 */
import { useMemo, useState } from 'react'
import { Camera, Check, CloudOff, RefreshCw, Wifi, X } from 'lucide-react'
import { Button, Card, Input, StatusChip } from '../components/ui/index.jsx'
import { useBarcodeCamera } from '../lib/useBarcodeCamera'
import { cleanCode, doorKey, useDoor } from './useDoor'
import './door.css'

const time = (iso) => iso ? new Date(iso).toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' }) : ''
const TONE = { ok: 'ok', already: 'warn', void: 'bad', wrong_event: 'bad', unknown: 'bad' }

function Result({ r }) {
  if (!r) return <div className="vdr-result vdr-result--idle" role="status">Scan a ticket</div>
  const t = r.ticket
  return (
    <div className={`vdr-result vdr-result--${TONE[r.result] || 'bad'}`} role="status" aria-live="assertive">
      <strong>{r.result === 'ok' && <Check size={28} aria-hidden="true" />}{r.message}</strong>
      {t && <span>{t.holder_name || 'No name'} · {t.type}</span>}
      {r.result === 'already' && t?.checked_in_at && <span>Used at {time(t.checked_in_at)}{t.checked_in_by ? ` · ${t.checked_in_by}` : ''}</span>}
      {r.offline && r.result === 'ok' && <span>No signal: saved on this phone and sent when it’s back.</span>}
    </div>
  )
}

export default function DoorPage() {
  const key = useMemo(doorKey, [])
  const d = useDoor(key)
  const [code, setCode] = useState('')
  const [last, setLast] = useState(null)
  const [q, setQ] = useState('')
  const [naming, setNaming] = useState(!d.device)

  const scan = async (raw) => {
    const c = cleanCode(raw)
    setCode('')
    if (!c) return setLast({ result: 'unknown', message: 'That isn’t one of our tickets.' })
    setLast(await d.checkIn({ code: c }))
  }
  const cam = useBarcodeCamera({ onCode: scan })

  if (!key || d.dead) {
    return <main className="vdr"><Card className="vdr-card"><h1 className="vdr-h1">This door link isn’t working</h1>
      <p className="vdr-sub">{d.dead || 'Open the link the organiser sent you again, or ask them for a new one.'}</p></Card></main>
  }
  if (!d.data) return <main className="vdr"><Card className="vdr-card"><p className="vdr-sub">{d.online ? 'Loading the door list…' : 'No signal. The door list loads the first time this phone has signal.'}</p></Card></main>

  const all = d.data.tickets
  const inCount = all.filter(t => t.status === 'checked_in').length
  const recent = all.filter(t => t.status === 'checked_in' && t.at).sort((a, b) => (a.at < b.at ? 1 : -1)).slice(0, 6)
  const term = q.trim().toLowerCase()
  const found = term ? all.filter(t => `${t.name} ${t.type}`.toLowerCase().includes(term)).slice(0, 30) : []
  return (
    <main className="vdr">
      <header className="vdr-head">
        <div className="vdr-main">
          <h1 className="vdr-h1">{d.data.event.title}</h1>
          <span className="vdr-sub">{d.data.event.venue}{d.device ? ` · ${d.device}` : ''}</span>
        </div>
        <StatusChip tone={d.online ? 'ok' : 'warn'}>{d.online ? <Wifi size={14} aria-hidden="true" /> : <CloudOff size={14} aria-hidden="true" />}{d.online ? 'Online' : 'No signal'}</StatusChip>
      </header>
      {d.queue.length > 0 && <p className="vdr-note" role="status">{d.queue.length} check-in{d.queue.length === 1 ? '' : 's'} saved on this phone, waiting for signal. Keep this page open.</p>}

      {naming ? (
        <Card className="vdr-card">
          <form className="vdr-row" onSubmit={e => { e.preventDefault(); setNaming(false) }}>
            <Input value={d.device} onChange={e => d.setDevice(e.target.value)} maxLength={40} placeholder="Which door is this? e.g. Gate A" aria-label="Which door is this" />
            <Button type="submit">Start</Button>
          </form>
        </Card>
      ) : (<>
        <div className="vdr-count"><span className="vula-figure">{inCount}</span><span className="vdr-sub">of {all.length} in</span></div>
        <Result r={last} />
        <Card className="vdr-card">
          <div className="vdr-video" hidden={!cam.on}><video ref={cam.videoRef} muted playsInline /></div>
          {cam.err && <p className="vdr-err" role="alert">{cam.err}</p>}
          <div className="vdr-row">
            <Button size="lg" variant={cam.on ? 'secondary' : 'primary'} Icon={cam.on ? X : Camera} onClick={() => cam.setOn(!cam.on)}>{cam.on ? 'Stop camera' : 'Scan with camera'}</Button>
            <Button variant="quiet" Icon={RefreshCw} onClick={d.refresh}>Refresh</Button>
          </div>
          <form className="vdr-row" onSubmit={e => { e.preventDefault(); scan(code) }}>
            <Input value={code} onChange={e => setCode(e.target.value)} placeholder="Or type the ticket code" aria-label="Ticket code" autoComplete="off" />
            <Button type="submit" variant="secondary" disabled={!code.trim()}>Check in</Button>
          </form>
        </Card>
        <Card className="vdr-card">
          <h2 className="vdr-h2">Find someone by name</h2>
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Type a name" aria-label="Find someone by name" />
          {term && (found.length === 0 ? <p className="vdr-sub">Nobody by that name has a ticket.</p> : (
            <ul className="vdr-list">
              {found.map(t => (
                <li key={t.id} className="vdr-att">
                  <span className="vdr-main"><strong>{t.name || 'No name'}</strong><span className="vdr-sub">{t.type}</span></span>
                  {t.status === 'checked_in'
                    ? <StatusChip tone="ok"><Check size={14} aria-hidden="true" />In {time(t.at)}</StatusChip>
                    : <Button size="sm" onClick={async () => setLast(await d.checkIn({ ticket: t }))}>Let in</Button>}
                </li>
              ))}
            </ul>
          ))}
        </Card>
        {recent.length > 0 && (
          <Card className="vdr-card">
            <h2 className="vdr-h2">Just came in</h2>
            <ul className="vdr-list">
              {recent.map(t => (
                <li key={t.id} className="vdr-att">
                  <span className="vdr-main"><strong>{t.name || 'No name'}</strong><span className="vdr-sub">{t.type}{t.by ? ` · ${t.by}` : ''}</span></span>
                  <span className="vdr-sub">{time(t.at)}{t.waiting ? ' · not sent yet' : ''}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </>)}
    </main>
  )
}
