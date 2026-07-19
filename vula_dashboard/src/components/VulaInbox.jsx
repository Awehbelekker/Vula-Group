/**
 * VulaInbox.jsx — shared inbox: live customer WhatsApp conversations.
 *
 * The owner sees every customer chat the AI is having, can open a thread,
 * TAKE OVER (bot goes quiet, they reply as themselves over WhatsApp), and
 * HAND BACK to the bot when done. Auto-refreshes so new messages appear.
 */

import { useState, useEffect, useCallback, useRef } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

export default function VulaInbox({ tenantId }) {
  const [convos, setConvos] = useState([])
  const [escalations, setEscalations] = useState([])
  const [loading, setLoading] = useState(true)
  const [active, setActive] = useState(null)      // session_id of open thread

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations`)
      const d = await r.json()
      setConvos(d.conversations || [])
    } catch {}
    setLoading(false)
  }, [tenantId])

  const loadEscalations = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations`)
      const d = await r.json()
      setEscalations(d.escalations || [])
    } catch {}
  }, [tenantId])

  useEffect(() => {
    load()
    loadEscalations()
    const t = setInterval(load, 15000)   // refresh list every 15s
    const te = setInterval(loadEscalations, 15000)
    return () => { clearInterval(t); clearInterval(te) }
  }, [load, loadEscalations])

  const since = ts => {
    if (!ts) return ''
    const mins = Math.floor((Date.now() - new Date(ts)) / 60000)
    if (mins < 1) return 'now'
    if (mins < 60) return `${mins}m`
    if (mins < 1440) return `${Math.floor(mins / 60)}h`
    return `${Math.floor(mins / 1440)}d`
  }

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
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  const since = ts => {
    if (!ts) return ''
    const mins = Math.floor((Date.now() - new Date(ts)) / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    if (mins < 1440) return `${Math.floor(mins / 60)}h ago`
    return `${Math.floor(mins / 1440)}d ago`
  }

  async function send() {
    const text = answer.trim()
    if (!text || busy) return
    setBusy(true)
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations/${esc.id}/answer`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: text }),
      })
      onDone()
    } catch {}
    setBusy(false)
  }

  async function dismiss() {
    if (busy) return
    setBusy(true)
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations/${esc.id}/dismiss`, { method: 'POST' })
      onDone()
    } catch {}
    setBusy(false)
  }

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
  const [data, setData] = useState(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [canned, setCanned] = useState([])
  const endRef = useRef(null)

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/canned-replies`)
      .then(r => r.json()).then(d => setCanned(d.canned_replies || [])).catch(() => {})
  }, [tenantId])

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/messages`)
      setData(await r.json())
    } catch {}
  }, [tenantId, sessionId])

  useEffect(() => {
    load()
    const t = setInterval(load, 8000)    // refresh thread every 8s
    return () => clearInterval(t)
  }, [load])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [data?.messages?.length])

  async function setHandoff(paused) {
    setBusy(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/handoff`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused }),
    })
    await load()
    setBusy(false)
  }

  async function sendReply(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setBusy(true); setInput('')
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/reply`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    })
    await load()
    setBusy(false)
  }

  async function saveMeta(patch) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/meta`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
    }).catch(() => {})
    load()
  }

  const msgs = data?.messages || []
  const paused = data?.paused

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
  h3:          { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: 'var(--ink, #1E1E1E)', margin: '0 0 4px' },
  sub:         { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  muted:       { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  list:        { display: 'flex', flexDirection: 'column', gap: 6 },
  row:         { display: 'flex', alignItems: 'center', gap: 12, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 10, padding: '12px 14px', cursor: 'pointer', width: '100%' },
  avatar:      { width: 38, height: 38, borderRadius: '50%', background: 'var(--accent, #2C5545)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'system-ui', fontWeight: 700, fontSize: 15, flexShrink: 0 },
  rowTop:      { display: 'flex', justifyContent: 'space-between', gap: 8 },
  name:        { fontFamily: 'system-ui', fontSize: 14, fontWeight: 600, color: '#1E1E1E', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  time:        { fontFamily: 'system-ui', fontSize: 11, color: '#B5B0A8', flexShrink: 0 },
  preview:     { fontFamily: 'system-ui', fontSize: 12, color: '#8A8680', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  pausedBadge: { fontFamily: 'system-ui', fontSize: 11, fontWeight: 600, color: '#b45309', background: 'rgba(245,158,11,0.15)', padding: '3px 8px', borderRadius: 10, flexShrink: 0 },
  escWrap:     { background: 'rgba(180,83,9,0.06)', border: '1px solid rgba(180,83,9,0.25)', borderRadius: 10, padding: 12, marginBottom: 14 },
  escHeader:   { fontFamily: 'system-ui', fontSize: 13, fontWeight: 700, color: '#b45309', marginBottom: 8 },
  escList:     { display: 'flex', flexDirection: 'column', gap: 8 },
  escRow:      { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 12px' },
  escTop:      { display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 },
  escQuestion: { fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E', marginBottom: 8, lineHeight: 1.4 },
  escInputRow: { display: 'flex', gap: 6 },
  escInput:    { flex: 1, padding: '7px 10px', border: '1px solid #DDD8CE', borderRadius: 6, fontFamily: 'system-ui', fontSize: 13, boxSizing: 'border-box' },
  escSendBtn:  { padding: '7px 12px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', flexShrink: 0 },
  escDismissBtn: { padding: '7px 10px', background: 'transparent', color: '#8A8680', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui', flexShrink: 0 },
  threadWrap:  { display: 'flex', flexDirection: 'column', height: 'calc(100vh - 190px)', minHeight: 420 },
  threadHeader:{ display: 'flex', alignItems: 'center', gap: 10, paddingBottom: 10, borderBottom: '1px solid #EDE9DF' },
  backBtn:     { padding: '6px 12px', background: 'transparent', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680', flexShrink: 0 },
  threadSub:   { display: 'block', fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  takeOverBtn: { padding: '8px 14px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', flexShrink: 0 },
  handBackBtn: { padding: '8px 14px', background: 'transparent', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', flexShrink: 0 },
  pausedNote:  { fontFamily: 'system-ui', fontSize: 12, color: '#b45309', background: 'rgba(245,158,11,0.1)', borderRadius: 6, padding: '8px 12px', margin: '10px 0 0' },
  thread:      { flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: '12px 2px' },
  msgRow:      { display: 'flex' },
  bubble:      { maxWidth: '78%', padding: '9px 13px', borderRadius: 13, fontFamily: 'system-ui', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap', display: 'flex', flexDirection: 'column', gap: 2 },
  inBubble:    { background: '#fff', color: '#1E1E1E', border: '1px solid #DDD8CE', borderBottomLeftRadius: 4 },
  outBubble:   { background: 'var(--accent-soft, rgba(44,85,69,0.10))', color: '#1E1E1E', borderBottomRightRadius: 4 },
  roleTag:     { fontSize: 10, fontWeight: 700, color: '#8A8680', letterSpacing: '0.04em' },
  metaRow:     { display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 0', borderBottom: '1px solid #EDE9DF', marginBottom: 4 },
  metaInput:   { flex: 1, minWidth: 90, padding: '5px 8px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 12, fontFamily: 'system-ui', color: '#3A3A3A' },
  cannedRow:   { display: 'flex', gap: 6, flexWrap: 'wrap', paddingTop: 10 },
  cannedChip:  { padding: '5px 10px', background: '#F3F1EA', border: '1px solid #DDD8CE', borderRadius: 14, fontSize: 12, color: '#3A3A3A', cursor: 'pointer', fontFamily: 'system-ui' },
  inputRow:    { display: 'flex', gap: 8, paddingTop: 10, borderTop: '1px solid #EDE9DF' },
  input:       { flex: 1, padding: '11px 14px', border: '1px solid #DDD8CE', borderRadius: 8, fontFamily: 'system-ui', fontSize: 14, boxSizing: 'border-box' },
  sendBtn:     { padding: '11px 20px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
}
