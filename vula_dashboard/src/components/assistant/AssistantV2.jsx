/**
 * AssistantV2.jsx — the redesigned "Chat to Vula" (2026-10-20, UX redesign Phase F). Same agent and
 * calls as VulaAssistant (assistant/useAssistantChat.js): the owner runs the shop by chatting,
 * and the agent takes real actions through the admin tools. Works full-page (Assistant tab) and
 * inside the Quick Launcher's sheet (inSheet).
 */
import { useRef } from 'react'
import { FileText, Globe, Paperclip, Send, Sparkles, ThumbsDown, ThumbsUp, X } from 'lucide-react'
import { Button, IconButton, Input, PillTabs } from '../ui/index.jsx'
import { SUGGESTIONS, useAssistantChat } from './useAssistantChat'
import MyVula from './MyVula'
import './assistant.css'

const MODES = [{ id: 'business', label: 'Your business' }, { id: 'open', label: 'Ask anything' }]

const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' } }

const ACCEPT = '.pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.md,.png,.jpg,.jpeg,.webp,.gif'

export default function AssistantV2({ tenantId, inSheet = false }) {
  const x = useAssistantChat(tenantId)
  const picker = useRef(null)
  return (
    <>
    <section className={`vas-chat${inSheet ? ' vas-chat--sheet' : ''}`} aria-label="Chat to Vula">
      {!inSheet && <p className="vas-sub">Your AI assistant runs the shop with you: ask about sales, orders, stock, invoices or expenses, or tell it to update something. Also on WhatsApp.</p>}
      {x.openAvailable && (
        <div className="vas-mode">
          <PillTabs label="Chat mode" tabs={MODES} active={x.mode} onChange={x.setMode} />
          {x.mode === 'open' && <p className="vas-hint">Ask anything: ideas, writing, planning. Answered on Vula's own AI; questions about your records still use them.</p>}
          {x.mode === 'open' && (
            <Button size="sm" variant={x.research ? 'primary' : 'secondary'} Icon={Globe} aria-pressed={x.research}
              onClick={() => x.setResearch(!x.research)}>Research the web</Button>)}
          {x.mode === 'open' && x.research && <p className="vas-hint">Vula searches the web, reads a few pages and your own documents, and lists its sources. Only the topic is searched, never names or numbers from your records. Takes about half a minute.</p>}
        </div>)}
      <ol className="vas-thread" aria-live="polite" aria-label="Conversation" tabIndex={0}>
        {x.messages.map((m, i) => (
          <li key={i} className={`vas-msg vas-msg--${m.role === 'user' ? 'me' : 'vula'}`}>
            <span className="vas-sr">{m.role === 'user' ? 'You' : 'Vula'}: </span>{m.text}
            {m.files?.length > 0 && <span className="vas-by">📎 {m.files.join(', ')}</span>}
            {m.answeredBy && x.openAvailable && (
              <span className="vas-by">{m.answeredBy === 'research' ? `Researched · ${m.research?.read || 0} pages read`
                : m.answeredBy === 'open' ? "Vula's own AI" : 'Answered with your business records'}</span>)}
            {(m.research?.sources?.length > 0 || m.research?.documents?.length > 0) && (
              <span className="vas-sources" role="group" aria-label="Sources">
                <span className="vas-by">Sources</span>
                <ol>
                  {m.research.sources.map(src => (
                    <li key={src.n} value={src.n}>
                      <a href={src.url} target="_blank" rel="noopener noreferrer">{src.title}</a>
                      <span className="vas-host"> — {hostOf(src.url)}</span>
                    </li>))}
                  {(m.research.documents || []).map(d => (
                    <li key={d.n} className="vas-doc">[{d.n}] {d.title} <span className="vas-host">(your documents)</span></li>))}
                </ol>
              </span>)}
            {m.confirm && (
              <span className="vas-confirm" role="group" aria-label="Confirm this action">
                <Button size="sm" disabled={m.confirm.busy} onClick={() => x.decide(m, 'confirm')}>{m.confirm.confirm_label || 'Confirm'}</Button>
                <Button size="sm" variant="secondary" disabled={m.confirm.busy} onClick={() => x.decide(m, 'cancel')}>{m.confirm.cancel_label || 'Cancel'}</Button>
              </span>)}
            {m.role !== 'user' && m.id && (
              <span className="vas-rate" role="group" aria-label="Rate this reply">
                <IconButton Icon={ThumbsUp} label="Good answer" quiet aria-pressed={m.rating === 'up'}
                  className={m.rating === 'up' ? 'vas-rate--on' : ''} onClick={() => x.rate(m, 'up')} />
                <IconButton Icon={ThumbsDown} label="Wrong or unhelpful" quiet aria-pressed={m.rating === 'down'}
                  className={m.rating === 'down' ? 'vas-rate--on' : ''} onClick={() => x.rate(m, 'down')} />
                {m.rateError && <span className="vas-warn" role="alert">Couldn't save that rating.</span>}
              </span>)}
          </li>))}
        {x.researching && <li className="vas-msg vas-msg--vula vas-hint" role="status">Researching — searching the web and reading pages…</li>}
        {x.sending && !x.researching && <li className="vas-msg vas-msg--vula vas-typing" aria-label="Vula is typing"><span /><span /><span /></li>}
        <li ref={x.endRef} aria-hidden="true" className="vas-end" />
      </ol>
      {x.messages.length <= 1 && (
        <div className="vas-suggest" role="group" aria-label="Try asking">
          {SUGGESTIONS.map(q => <Button key={q} size="sm" variant="secondary" Icon={Sparkles} onClick={() => x.send(q)}>{q}</Button>)}
        </div>)}
      {(x.files.length > 0 || x.fileError) && (
        <div className="vas-files" role="group" aria-label="Attached files">
          {x.files.map(f => (
            <span key={f.id || f.tempId} className="vas-file">
              <FileText size={14} aria-hidden="true" />
              <span>{f.name}{f.reading ? ' — reading…' : f.truncated ? ' — long file, the start is read' : ''}</span>
              {f.id && !f.filed && <Button size="sm" variant="secondary" onClick={() => x.keepFile(f.id)}>Keep in our documents</Button>}
              {f.filed && <span className="vas-hint">In your documents</span>}
              {f.id && <IconButton Icon={X} label={`Remove ${f.name}`} quiet onClick={() => x.removeFile(f.id)} />}
            </span>))}
          {x.fileError && <p className="vas-warn" role="alert">{x.fileError}</p>}
        </div>)}
      <form className="vas-compose" onSubmit={e => { e.preventDefault(); x.send() }}>
        <input ref={picker} type="file" multiple accept={ACCEPT} hidden aria-hidden="true" tabIndex={-1}
          onChange={e => { x.attach(e.target.files); e.target.value = '' }} />
        <IconButton Icon={Paperclip} label="Attach a file" quiet disabled={x.sending || x.files.length >= 3}
          onClick={() => picker.current?.click()} />
        <Input value={x.input} onChange={e => x.setInput(e.target.value)} disabled={x.sending}
          aria-label="Message Vula" placeholder={x.openAvailable && x.mode === 'open' ? 'Ask anything — ideas, a draft, a plan…' : 'Ask, or tell Vula to do something…'} />
        <Button type="submit" Icon={Send} disabled={x.sending || (!x.input.trim() && !x.files.some(f => f.id)) || x.files.some(f => f.reading)}>Send</Button>
      </form>
    </section>
    {/* Below the chat, not inside it: the chat is a fixed-height column, and on a phone anything
        opened inside it ended up under the bottom bar. */}
    {!inSheet && <MyVula tenantId={tenantId} />}
    </>
  )
}
