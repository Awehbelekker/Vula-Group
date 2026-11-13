/**
 * AssistantV2.jsx — the redesigned "Chat to Vula" (2026-10-20, UX redesign Phase F). Same agent and
 * calls as VulaAssistant (assistant/useAssistantChat.js): the owner runs the shop by chatting,
 * and the agent takes real actions through the admin tools. Works full-page (Assistant tab) and
 * inside the Quick Launcher's sheet (inSheet).
 */
import { Send, Sparkles, ThumbsDown, ThumbsUp } from 'lucide-react'
import { Button, IconButton, Input } from '../ui/index.jsx'
import { SUGGESTIONS, useAssistantChat } from './useAssistantChat'
import MyVula from './MyVula'
import './assistant.css'

export default function AssistantV2({ tenantId, inSheet = false }) {
  const x = useAssistantChat(tenantId)
  return (
    <>
    <section className={`vas-chat${inSheet ? ' vas-chat--sheet' : ''}`} aria-label="Chat to Vula">
      {!inSheet && <p className="vas-sub">Your AI assistant runs the shop with you: ask about sales, orders, stock, invoices or expenses, or tell it to update something. Also on WhatsApp.</p>}
      <ol className="vas-thread" aria-live="polite" aria-label="Conversation">
        {x.messages.map((m, i) => (
          <li key={i} className={`vas-msg vas-msg--${m.role === 'user' ? 'me' : 'vula'}`}>
            <span className="vas-sr">{m.role === 'user' ? 'You' : 'Vula'}: </span>{m.text}
            {m.role !== 'user' && m.id && (
              <span className="vas-rate" role="group" aria-label="Rate this reply">
                <IconButton Icon={ThumbsUp} label="Good answer" quiet aria-pressed={m.rating === 'up'}
                  className={m.rating === 'up' ? 'vas-rate--on' : ''} onClick={() => x.rate(m, 'up')} />
                <IconButton Icon={ThumbsDown} label="Wrong or unhelpful" quiet aria-pressed={m.rating === 'down'}
                  className={m.rating === 'down' ? 'vas-rate--on' : ''} onClick={() => x.rate(m, 'down')} />
                {m.rateError && <span className="vas-warn" role="alert">Couldn't save that rating.</span>}
              </span>)}
          </li>))}
        {x.sending && <li className="vas-msg vas-msg--vula vas-typing" aria-label="Vula is typing"><span /><span /><span /></li>}
        <li ref={x.endRef} aria-hidden="true" className="vas-end" />
      </ol>
      {x.messages.length <= 1 && (
        <div className="vas-suggest" role="group" aria-label="Try asking">
          {SUGGESTIONS.map(q => <Button key={q} size="sm" variant="secondary" Icon={Sparkles} onClick={() => x.send(q)}>{q}</Button>)}
        </div>)}
      <form className="vas-compose" onSubmit={e => { e.preventDefault(); x.send() }}>
        <Input value={x.input} onChange={e => x.setInput(e.target.value)} disabled={x.sending}
          aria-label="Message Vula" placeholder="Ask, or tell Vula to do something…" />
        <Button type="submit" Icon={Send} disabled={x.sending || !x.input.trim()}>Send</Button>
      </form>
    </section>
    {/* Below the chat, not inside it: the chat is a fixed-height column, and on a phone anything
        opened inside it ended up under the bottom bar. */}
    {!inSheet && <MyVula tenantId={tenantId} />}
    </>
  )
}
