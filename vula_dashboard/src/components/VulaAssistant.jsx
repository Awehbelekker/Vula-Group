/**
 * VulaAssistant.jsx — "Chat to Vula" in-portal admin assistant.
 *
 * Same AI agent that powers the owner's WhatsApp admin (commerce_admin skill),
 * available right inside the dashboard. The owner can run the shop by chatting:
 * sales, orders, stock, invoices, expenses, broadcast previews — and it takes
 * real actions via the admin tools.
 */
import { SUGGESTIONS, useAssistantChat } from './assistant/useAssistantChat'
import AssistantV2 from './assistant/AssistantV2'

export default function VulaAssistant({ v2 = false, ...props }) {
  return v2 ? <AssistantV2 {...props} /> : <VulaAssistantV1 {...props} />
}

function VulaAssistantV1({ tenantId }) {
  const { messages, input, setInput, sending, endRef, send } = useAssistantChat(tenantId)

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
  h3:          { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:         { fontSize: 13, color: 'var(--muted)', margin: 0 },
  thread:      { flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 2px' },
  row:         { display: 'flex' },
  bubble:      { maxWidth: '80%', padding: '10px 14px', borderRadius: 14, fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' },
  userBubble:  { background: 'var(--accent, var(--accent))', color: '#fff', borderBottomRightRadius: 4 },
  botBubble:   { background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--border)', borderBottomLeftRadius: 4 },
  suggestions: { display: 'flex', gap: 6, flexWrap: 'wrap', margin: '10px 0' },
  suggestion:  { padding: '7px 12px', borderRadius: 16, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer', fontSize: 12, color: 'var(--accent, var(--accent))' },
  inputRow:    { display: 'flex', gap: 8, paddingTop: 10, borderTop: '1px solid var(--border-soft)' },
  input:       { flex: 1, padding: '11px 14px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 14, boxSizing: 'border-box' },
  sendBtn:     { padding: '11px 20px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
}
