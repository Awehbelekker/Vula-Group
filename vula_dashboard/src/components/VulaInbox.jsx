/**
 * VulaInbox.jsx — shared inbox: live customer WhatsApp conversations.
 *
 * The owner sees every customer chat the AI is having, can open a thread,
 * TAKE OVER (bot goes quiet, they reply as themselves over WhatsApp), and
 * HAND BACK to the bot when done. Auto-refreshes so new messages appear.
 * Data and actions live in inbox/useInbox.js (shared with the redesign's inbox/InboxV2.jsx).
 */

import { useState, useEffect, useRef } from 'react'
import { since as sinceShort, useEscalation, useInboxList, useThread } from './inbox/useInbox'
import InboxV2 from './inbox/InboxV2'


/** v2: the redesigned Inbox (same hooks, so the same data and actions). */
export default function VulaInbox({ tenantId, v2 = false }) {
  if (v2) return <InboxV2 tenantId={tenantId} />
  return <InboxV1 tenantId={tenantId} />
}

function InboxV1({ tenantId }) {
  const { convos, escalations, loading, load, loadEscalations } = useInboxList(tenantId)
  const [active, setActive] = useState(null)      // session_id of open thread
  const since = sinceShort

  if (active) {
    return <Thread tenantId={tenantId} sessionId={active} onBack={() => { setActive(null); load() }} />
  }

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>📥 Inbox</h3>
        <p style={s.sub}>Every customer chat, live. Open one to read it — or take over from the AI.</p>
      </div>

      {escalations.length > 0 && (
        <div style={s.escWrap}>
          <div style={s.escHeader}>❓ Waiting on you ({escalations.length})</div>
          <div style={s.escList}>
            {escalations.map(e => (
              <EscalationRow key={e.id} tenantId={tenantId} esc={e} onDone={loadEscalations} />
            ))}
          </div>
        </div>
      )}

      {loading ? <p style={s.muted}>Loading…</p> : convos.length === 0 ? (
        <p style={s.muted}>No conversations yet. When customers message your WhatsApp line, they'll appear here.</p>
      ) : (
        <div style={s.list}>
          {convos.map(c => (
            <button key={c.session_id} onClick={() => setActive(c.session_id)} style={s.row}>
              <div style={s.avatar}>{(c.customer_name || c.customer_phone || '?').charAt(0).toUpperCase()}</div>
              <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                <div style={s.rowTop}>
                  <span style={s.name}>{c.customer_name || c.customer_phone}</span>
                  <span style={s.time}>{since(c.last_at)}</span>
                </div>
                <div style={s.preview}>
                  {c.last_role === 'assistant' ? '🤖 ' : c.last_role === 'agent' ? '🧑 ' : ''}
                  {c.last_message || '—'}
                </div>
              </div>
              {c.paused && <span style={s.pausedBadge}>🧑 You</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── One open escalation — answer or dismiss straight from the dashboard ───────

function EscalationRow({ tenantId, esc, onDone }) {
  const { answer, setAnswer, busy, send, dismiss } = useEscalation(tenantId, esc, onDone)
  const since = ts => sinceShort(ts, true)

  return (
    <div style={s.escRow}>
      <div style={s.escTop}>
        <span style={s.name}>{esc.customer_phone}</span>
        <span style={s.time}>{since(esc.created_at)}</span>
      </div>
      <div style={s.escQuestion}>{esc.question}</div>
      <div style={s.escInputRow}>
        <input
          value={answer}
          onChange={e => setAnswer(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
          placeholder="Type the answer to send…"
          style={s.escInput}
          disabled={busy}
        />
        <button onClick={send} disabled={busy || !answer.trim()} style={s.escSendBtn}>Send</button>
        <button onClick={dismiss} disabled={busy} style={s.escDismissBtn}>Dismiss</button>
      </div>
    </div>
  )
}

// ── Single conversation thread ────────────────────────────────────────────────

function Thread({ tenantId, sessionId, onBack }) {
  const { data, msgs, paused, input, setInput, busy, canned, rated, rate, setHandoff, sendReply, saveMeta } =
    useThread(tenantId, sessionId)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [data?.messages?.length])

  return (
    <div style={s.threadWrap}>
      <div style={s.threadHeader}>
        <button onClick={onBack} style={s.backBtn}>← Inbox</button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <span style={s.name}>{data?.customer || data?.phone || '…'}</span>
          <span style={s.threadSub}>{data?.phone}</span>
        </div>
        {paused ? (
          <button disabled={busy} onClick={() => setHandoff(false)} style={s.handBackBtn}>
            🤖 Hand back to AI
          </button>
        ) : (
          <button disabled={busy} onClick={() => setHandoff(true)} style={s.takeOverBtn}>
            🧑 Take over
          </button>
        )}
      </div>

      {paused && (
        <p style={s.pausedNote}>You've taken over — the AI is quiet. Replies below go straight to the customer on WhatsApp.</p>
      )}

      <div style={s.metaRow}>
        <input key={`asg-${sessionId}`} defaultValue={data?.assigned_to || ''} placeholder="👤 Assign to…"
          onBlur={e => e.target.value !== (data?.assigned_to || '') && saveMeta({ assigned_to: e.target.value })}
          style={s.metaInput} />
        <input key={`tag-${sessionId}`} defaultValue={(data?.tags || []).join(', ')} placeholder="🏷️ tags (comma)"
          onBlur={e => saveMeta({ tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })}
          style={s.metaInput} />
        <input key={`note-${sessionId}`} defaultValue={data?.agent_note || ''} placeholder="📝 internal note (not sent)"
          onBlur={e => e.target.value !== (data?.agent_note || '') && saveMeta({ agent_note: e.target.value })}
          style={{ ...s.metaInput, flex: 2 }} />
      </div>

      <div style={s.thread}>
        {msgs.map((m, i) => {
          const mine = m.role !== 'user'
          return (
            <div key={i} style={{ ...s.msgRow, justifyContent: mine ? 'flex-end' : 'flex-start' }}>
              <div style={{ ...s.bubble, ...(mine ? s.outBubble : s.inBubble) }}>
                {m.role === 'assistant' && <span style={s.roleTag}>🤖 AI</span>}
                {m.role === 'agent' && <span style={s.roleTag}>🧑 You</span>}
                <span>{m.content}</span>
                {m.role === 'assistant' && m.id && (
                  <span style={{ display: 'flex', gap: 6, marginTop: 4, justifyContent: 'flex-end' }}>
                    <button type="button" title="Good reply" onClick={() => rate(m, i, 'up')}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', opacity: rated[m.id] === 'down' ? 0.35 : 1 }}>👍</button>
                    <button type="button" title="Wrong or unhelpful — correct it" onClick={() => rate(m, i, 'down')}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', opacity: rated[m.id] === 'up' ? 0.35 : 1 }}>👎</button>
                  </span>
                )}
              </div>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>

      {canned.length > 0 && (
        <div style={s.cannedRow}>
          {canned.map(c => (
            <button key={c.id} type="button" title={c.body} style={s.cannedChip}
              onClick={() => setInput(c.body)}>{c.title}</button>
          ))}
        </div>
      )}

      <form onSubmit={sendReply} style={s.inputRow}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder={paused ? 'Reply to the customer…' : 'Type to take over and reply…'}
          style={s.input}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()} style={s.sendBtn}>Send</button>
      </form>
    </div>
  )
}

const s = {
  intro:       { marginBottom: 14 },
  h3:          { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:         { fontSize: 13, color: 'var(--muted)', margin: 0 },
  muted:       { color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '24px 0' },
  list:        { display: 'flex', flexDirection: 'column', gap: 6 },
  row:         { display: 'flex', alignItems: 'center', gap: 12, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', cursor: 'pointer', width: '100%' },
  avatar:      { width: 38, height: 38, borderRadius: '50%', background: 'var(--accent)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 15, flexShrink: 0 },
  rowTop:      { display: 'flex', justifyContent: 'space-between', gap: 8 },
  name:        { fontSize: 14, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  time:        { fontSize: 11, color: 'var(--faint)', flexShrink: 0 },
  preview:     { fontSize: 12, color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  pausedBadge: { fontSize: 11, fontWeight: 600, color: 'var(--warn)', background: 'rgba(245,158,11,0.15)', padding: '3px 8px', borderRadius: 10, flexShrink: 0 },
  escWrap:     { background: 'rgba(180,83,9,0.06)', border: '1px solid rgba(180,83,9,0.25)', borderRadius: 10, padding: 12, marginBottom: 14 },
  escHeader:   { fontSize: 13, fontWeight: 700, color: 'var(--warn)', marginBottom: 8 },
  escList:     { display: 'flex', flexDirection: 'column', gap: 8 },
  escRow:      { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' },
  escTop:      { display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 },
  escQuestion: { fontSize: 13, color: 'var(--ink)', marginBottom: 8, lineHeight: 1.4 },
  escInputRow: { display: 'flex', gap: 6 },
  escInput:    { flex: 1, padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box' },
  escSendBtn:  { padding: '7px 12px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', flexShrink: 0 },
  escDismissBtn: { padding: '7px 10px', background: 'transparent', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12, cursor: 'pointer', flexShrink: 0 },
  threadWrap:  { display: 'flex', flexDirection: 'column', height: 'calc(100vh - 190px)', minHeight: 420 },
  threadHeader:{ display: 'flex', alignItems: 'center', gap: 10, paddingBottom: 10, borderBottom: '1px solid var(--border-soft)' },
  backBtn:     { padding: '6px 12px', background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, cursor: 'pointer', color: 'var(--muted)', flexShrink: 0 },
  threadSub:   { display: 'block', fontSize: 11, color: 'var(--muted)' },
  takeOverBtn: { padding: '8px 14px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', flexShrink: 0 },
  handBackBtn: { padding: '8px 14px', background: 'transparent', color: 'var(--accent)', border: '1px solid var(--accent)', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', flexShrink: 0 },
  pausedNote:  { fontSize: 12, color: 'var(--warn)', background: 'rgba(245,158,11,0.1)', borderRadius: 6, padding: '8px 12px', margin: '10px 0 0' },
  thread:      { flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: '12px 2px' },
  msgRow:      { display: 'flex' },
  bubble:      { maxWidth: '78%', padding: '9px 13px', borderRadius: 13, fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap', display: 'flex', flexDirection: 'column', gap: 2 },
  inBubble:    { background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--border)', borderBottomLeftRadius: 4 },
  outBubble:   { background: 'var(--accent-soft, rgba(44,85,69,0.10))', color: 'var(--ink)', borderBottomRightRadius: 4 },
  roleTag:     { fontSize: 10, fontWeight: 700, color: 'var(--muted)', letterSpacing: '0.04em' },
  metaRow:     { display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 0', borderBottom: '1px solid var(--border-soft)', marginBottom: 4 },
  metaInput:   { flex: 1, minWidth: 90, padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12, color: 'var(--text)' },
  cannedRow:   { display: 'flex', gap: 6, flexWrap: 'wrap', paddingTop: 10 },
  cannedChip:  { padding: '5px 10px', background: '#F3F1EA', border: '1px solid var(--border)', borderRadius: 14, fontSize: 12, color: 'var(--text)', cursor: 'pointer'},
  inputRow:    { display: 'flex', gap: 8, paddingTop: 10, borderTop: '1px solid var(--border-soft)' },
  input:       { flex: 1, padding: '11px 14px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 14, boxSizing: 'border-box' },
  sendBtn:     { padding: '11px 20px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
}
