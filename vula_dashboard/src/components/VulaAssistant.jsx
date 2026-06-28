/**
 * VulaAssistant.jsx — "Chat to Vula" in-portal admin assistant.
 *
 * Same AI agent that powers the owner's WhatsApp admin (commerce_admin skill),
 * available right inside the dashboard. The owner can run the shop by chatting:
 * sales, orders, stock, invoices, expenses, broadcast previews — and it takes
 * real actions via the admin tools.
 */

import { useState, useRef, useEffect } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const SUGGESTIONS = [
  "What were today's sales?",
  "Which orders need dispatching?",
  "What's low on stock?",
  "What invoices are outstanding?",
]

export default function VulaAssistant({ tenantId }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: "Hi! I'm your Vula assistant. Ask me about sales, orders, stock, invoices or expenses — or tell me to update something." },
  ])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const sessionId = useRef(`${tenantId}-${Math.random().toString(36).slice(2, 9)}`)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, sending])

  async function send(text) {
    const msg = (text ?? input).trim()
    if (!msg || sending) return
    setInput('')
    setMessages(m => [...m, { role: 'user', text: msg }])
    setSending(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, session_id: sessionId.current }),
      })
      const d = await r.json()
      setMessages(m => [...m, { role: 'assistant', text: d.answer || "Sorry, I couldn't process that." }])
    } catch {
      setMessages(m => [...m, { role: 'assistant', text: 'Connection error — please try again.' }])
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <h3 style={s.h3}>💬 Chat to Vula</h3>
        <p style={s.sub}>Your AI assistant — runs the shop with you. Also available on WhatsApp.</p>
      </div>

      <div style={s.thread}>
        {messages.map((m, i) => (
          <div key={i} style={{ ...s.row, justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <div style={{ ...s.bubble, ...(m.role === 'user' ? s.userBubble : s.botBubble) }}>
              {m.text}
            </div>
          </div>
        ))}
        {sending && (
          <div style={{ ...s.row, justifyContent: 'flex-start' }}>
            <div style={{ ...s.bubble, ...s.botBubble }}>…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {messages.length <= 1 && (
        <div style={s.suggestions}>
          {SUGGESTIONS.map(q => (
            <button key={q} onClick={() => send(q)} style={s.suggestion}>{q}</button>
          ))}
        </div>
      )}

      <form onSubmit={e => { e.preventDefault(); send() }} style={s.inputRow}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask or tell me to do something…"
          style={s.input}
          disabled={sending}
        />
        <button type="submit" disabled={sending || !input.trim()} style={s.sendBtn}>Send</button>
      </form>
    </div>
  )
}

const s = {
  wrap:        { display: 'flex', flexDirection: 'column', height: 'calc(100vh - 200px)', minHeight: 420 },
  header:      { marginBottom: 12 },
  h3:          { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: 'var(--ink, #1E1E1E)', margin: '0 0 4px' },
  sub:         { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  thread:      { flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 2px' },
  row:         { display: 'flex' },
  bubble:      { maxWidth: '80%', padding: '10px 14px', borderRadius: 14, fontFamily: 'system-ui', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' },
  userBubble:  { background: 'var(--accent, var(--accent))', color: '#fff', borderBottomRightRadius: 4 },
  botBubble:   { background: '#fff', color: '#1E1E1E', border: '1px solid #DDD8CE', borderBottomLeftRadius: 4 },
  suggestions: { display: 'flex', gap: 6, flexWrap: 'wrap', margin: '10px 0' },
  suggestion:  { padding: '7px 12px', borderRadius: 16, border: '1px solid #DDD8CE', background: '#fff', cursor: 'pointer', fontSize: 12, fontFamily: 'system-ui', color: 'var(--accent, var(--accent))' },
  inputRow:    { display: 'flex', gap: 8, paddingTop: 10, borderTop: '1px solid #EDE9DF' },
  input:       { flex: 1, padding: '11px 14px', border: '1px solid #DDD8CE', borderRadius: 8, fontFamily: 'system-ui', fontSize: 14, boxSizing: 'border-box' },
  sendBtn:     { padding: '11px 20px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
}
