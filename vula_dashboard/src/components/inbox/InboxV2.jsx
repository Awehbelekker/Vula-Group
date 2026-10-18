/**
 * InboxV2.jsx — the redesigned Inbox (2026-10-18, UX redesign Phase F). Same data and actions as
 * the current Inbox (inbox/useInbox.js); only the layout is new. From 1024px a two-pane view:
 * the chats on the left, the open one on the right. Narrower, the list or the open chat. Search
 * and the chips filter the list already loaded; nothing extra is fetched.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Bot, MessagesSquare, Send, ThumbsDown, ThumbsUp, UserRound } from 'lucide-react'
import { Button, Card, EmptyState, FilterBar, IconButton, Input, ListRow, PageHeader, Skeleton, StatusChip } from '../ui/index.jsx'
import { useBreakpoint } from '../../hooks/useBreakpoint'
import { since, useEscalation, useInboxList, useThread } from './useInbox'
import './inbox.css'

const initial = (c) => (c.customer_name || c.customer_phone || '?').charAt(0).toUpperCase()

export default function InboxV2({ tenantId }) {
  const { convos, escalations, loading, load, loadEscalations } = useInboxList(tenantId)
  const [active, setActive] = useState(null)      // session_id of the open chat
  const [query, setQuery] = useState('')
  const [chips, setChips] = useState([])

  const waitingPhones = useMemo(() => new Set(escalations.map(e => e.customer_phone)), [escalations])
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return convos.filter(c =>
      (!q || `${c.customer_name || ''} ${c.customer_phone || ''}`.toLowerCase().includes(q)) &&
      (!chips.includes('waiting') || waitingPhones.has(c.customer_phone)) &&
      (!chips.includes('mine') || c.paused))
  }, [convos, query, chips, waitingPhones])

  const toggle = (id) => setChips(cur => (cur.includes(id) ? cur.filter(c => c !== id) : [...cur, id]))
  const close = () => { setActive(null); load() }

  return (
    <div className="vin" data-open={active ? 'true' : 'false'}>
      {/* the shell's top bar already says "Inbox" */}
      <PageHeader sub="Every customer chat, live. Open one to read it, or take over from Vula." />
      <div className="vin-panes">
        <section className="vin-list" aria-label="Chats">
          <FilterBar query={query} onQuery={setQuery} placeholder="Search by name or number" label="Show"
            chips={[
              { id: 'waiting', label: 'Waiting on you', count: escalations.length },
              { id: 'mine', label: 'You took over', count: convos.filter(c => c.paused).length },
            ]}
            selected={chips} onToggle={toggle} />

          {escalations.length > 0 && (
            <Card pad={0} className="vin-esc">
              <h2 className="vin-esc-head">Waiting on you ({escalations.length})</h2>
              {escalations.map(e => (
                <EscalationRow key={e.id} tenantId={tenantId} esc={e} onDone={loadEscalations} />
              ))}
            </Card>
          )}

          {loading ? <Skeleton lines={5} height={18} /> : convos.length === 0 ? (
            <EmptyState Icon={MessagesSquare} title="No chats yet">
              When customers message your WhatsApp line, they'll appear here.
            </EmptyState>
          ) : shown.length === 0 ? (
            <EmptyState Icon={MessagesSquare} title="No chats match">Try another name or clear the filters.</EmptyState>
          ) : (
            <Card pad={4} role="list">
              {shown.map(c => (
                <div role="listitem" key={c.session_id}>
                  <ListRow initials={initial(c)} title={c.customer_name || c.customer_phone}
                    className={active === c.session_id ? 'vin-row--on' : undefined}
                    aria-current={active === c.session_id ? 'true' : undefined}
                    detail={`${c.last_role === 'assistant' ? 'Vula: ' : c.last_role === 'agent' ? 'You: ' : ''}${c.last_message || '—'}`}
                    trailing={<>
                      {waitingPhones.has(c.customer_phone) && <StatusChip tone="warm">Waiting</StatusChip>}
                      {c.paused && <StatusChip tone="warn">You</StatusChip>}
                      <span>{since(c.last_at)}</span>
                    </>}
                    onClick={() => setActive(c.session_id)} />
                </div>
              ))}
            </Card>
          )}
        </section>

        <section className="vin-thread" aria-label="Open chat">
          {active
            ? <ThreadV2 key={active} tenantId={tenantId} sessionId={active} onBack={close} />
            : <EmptyState Icon={MessagesSquare} title="Pick a chat">Open a chat on the left to read it or reply.</EmptyState>}
        </section>
      </div>
    </div>
  )
}

function EscalationRow({ tenantId, esc, onDone }) {
  const { answer, setAnswer, busy, send, dismiss } = useEscalation(tenantId, esc, onDone)
  return (
    <div className="vin-esc-row">
      <div className="vin-esc-top">
        <span className="vin-name">{esc.customer_phone}</span>
        <span className="vin-time">{since(esc.created_at, true)}</span>
      </div>
      <p className="vin-esc-q">{esc.question}</p>
      <div className="vin-esc-form">
        <Input value={answer} onChange={e => setAnswer(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()}
          placeholder="Type the answer to send…" aria-label={`Answer for ${esc.customer_phone}`} disabled={busy} />
        <div className="vin-esc-actions">
          <Button size="sm" onClick={send} disabled={busy || !answer.trim()}>Send</Button>
          <Button size="sm" variant="secondary" onClick={dismiss} disabled={busy}>Dismiss</Button>
        </div>
      </div>
    </div>
  )
}

function ThreadV2({ tenantId, sessionId, onBack }) {
  const { data, msgs, paused, input, setInput, busy, canned, rated, rate, setHandoff, sendReply, saveMeta } =
    useThread(tenantId, sessionId)
  const phone = useBreakpoint() === 'mobile'
  const meta = (
    <div className="vin-meta">
      <Input key={`asg-${sessionId}`} defaultValue={data?.assigned_to || ''} placeholder="Assign to…" aria-label="Assign to"
        onBlur={e => e.target.value !== (data?.assigned_to || '') && saveMeta({ assigned_to: e.target.value })} />
      <Input key={`tag-${sessionId}`} defaultValue={(data?.tags || []).join(', ')} placeholder="Tags (comma)" aria-label="Tags"
        onBlur={e => saveMeta({ tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })} />
      <Input key={`note-${sessionId}`} defaultValue={data?.agent_note || ''} placeholder="Internal note (not sent)" aria-label="Internal note"
        className="vin-meta-note"
        onBlur={e => e.target.value !== (data?.agent_note || '') && saveMeta({ agent_note: e.target.value })} />
    </div>
  )
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [data?.messages?.length])

  return (
    <div className="vin-chat">
      <header className="vin-chat-head">
        <IconButton Icon={ArrowLeft} label="Back to chats" quiet className="vin-back" onClick={onBack} />
        <div className="vin-chat-who">
          <h2 className="vin-name">{data?.customer || data?.phone || '…'}</h2>
          {data?.phone && <span className="vin-time">{data.phone}</span>}
        </div>
        {paused ? (
          <Button size="sm" variant="secondary" Icon={Bot} disabled={busy} onClick={() => setHandoff(false)}>Hand back to Vula</Button>
        ) : (
          <Button size="sm" Icon={UserRound} disabled={busy} onClick={() => setHandoff(true)}>Take over</Button>
        )}
      </header>

      {paused && (
        <p className="vin-paused" role="status">You've taken over — Vula is quiet. Replies below go straight to the customer on WhatsApp.</p>
      )}

      {/* on a phone: folded away so the messages get the screen */}
      {phone ? <details className="vin-details"><summary>Assign, tags and note</summary>{meta}</details> : meta}

      <div className="vin-msgs" role="log" aria-live="polite" aria-label="Messages">
        {!data && <Skeleton lines={4} height={18} />}
        {msgs.map((m, i) => {
          const mine = m.role !== 'user'
          return (
            <div key={i} className={`vin-msg${mine ? ' vin-msg--out' : ''}`}>
              <div className="vin-bubble">
                {m.role === 'assistant' && <span className="vin-role">Vula</span>}
                {m.role === 'agent' && <span className="vin-role">You</span>}
                <span>{m.content}</span>
                {m.role === 'assistant' && m.id && (
                  <span className="vin-rate">
                    <IconButton Icon={ThumbsUp} label="Good reply" quiet aria-pressed={rated[m.id] === 'up'}
                      onClick={() => rate(m, i, 'up')} />
                    <IconButton Icon={ThumbsDown} label="Wrong or unhelpful — correct it" quiet aria-pressed={rated[m.id] === 'down'}
                      onClick={() => rate(m, i, 'down')} />
                  </span>
                )}
              </div>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>

      <div className="vin-compose">
        {canned.length > 0 && (
          <div className="vui-chips vin-canned" role="group" aria-label="Saved replies">
            {canned.map(c => (
              <button key={c.id} type="button" title={c.body} className="vui-chip" onClick={() => setInput(c.body)}>{c.title}</button>
            ))}
          </div>
        )}
        <form onSubmit={sendReply} className="vin-form">
          <Input value={input} onChange={e => setInput(e.target.value)} disabled={busy}
            placeholder={paused ? 'Reply to the customer…' : 'Type to take over and reply…'} aria-label="Reply" />
          <Button type="submit" Icon={Send} disabled={busy || !input.trim()}>Send</Button>
        </form>
      </div>
    </div>
  )
}
