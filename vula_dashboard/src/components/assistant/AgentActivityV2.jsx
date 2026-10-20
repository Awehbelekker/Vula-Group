/**
 * AgentActivityV2.jsx — the redesigned Assistant › Agent activity (2026-10-20, UX redesign Phase F).
 * Same calls as VulaAgentActivity (assistant/useAgentActivity.js): how Vula sounds (the persona,
 * set by hand or learned from the owner's own replies), teaching it a correct answer, and the
 * timeline of what the AI did: the tools it called (consequential ones marked) and whether it ran
 * on the local GPU or escalated to the cloud.
 */
import { AlertTriangle, BookOpenCheck, Check, Cloud, Cpu, MessageSquareText, RefreshCw, Sparkles, Wrench, X } from 'lucide-react'
import { SA_TZ } from '../../lib/dates'
import { Button, Card, EmptyState, Field, Input, Skeleton, Textarea } from '../ui/index.jsx'
import { useAgentActivity } from './useAgentActivity'
import './assistant.css'

const plain = (t) => String(t || '').replace(/\s*✓$/, '')
const ago = (ts) => {
  if (!ts) return ''
  const d = new Date(ts), s = Math.floor((Date.now() - d) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', timeZone: SA_TZ })
}

export default function AgentActivityV2({ tenantId }) {
  const x = useAgentActivity(tenantId)
  return (
    <section className="vas" aria-label="Agent activity">
      <p className="vas-sub">See what Vula's AI did: the tools it used, and whether it ran on your local GPU or escalated to the cloud.</p>

      <Card className="vas-card" role="group" aria-labelledby="vas-voice">
        <h2 className="vas-h" id="vas-voice"><MessageSquareText size={18} aria-hidden="true" />How Vula sounds</h2>
        <p className="vas-muted">Set it yourself, or let Vula learn it from your own words: WhatsApp replies you've sent, meeting notes and emails.</p>
        <Field label="Voice"><Textarea rows={3} value={x.persona} onChange={e => x.setPersona(e.target.value)}
          placeholder="e.g. Warm, casual, quick replies. Small family shop, not corporate." /></Field>
        <div className="vas-row">
          <Button Icon={Check} onClick={() => x.savePersona(x.persona)}>Save</Button>
          <Button variant="secondary" Icon={Sparkles} disabled={x.analyzing} onClick={x.analyzeVoice}>{x.analyzing ? 'Analysing…' : 'Analyse my voice'}</Button>
          {x.voiceMsg && <span className="vas-ok" role="status">{plain(x.voiceMsg)}</span>}
        </div>
        {x.suggested && x.suggested !== x.persona && (
          <div className="vas-suggestion" role="group" aria-label="Vula suggests">
            <span className="vas-muted">Vula suggests:</span>
            <p className="vas-body">{x.suggested}</p>
            <div className="vas-row">
              <Button size="sm" Icon={Check} onClick={() => x.savePersona(x.suggested)}>Accept</Button>
              <Button size="sm" variant="secondary" Icon={X} onClick={() => x.savePersona(x.persona)}>Dismiss</Button>
            </div>
          </div>)}
      </Card>

      <Card className="vas-card" role="group" aria-labelledby="vas-teach">
        <h2 className="vas-h" id="vas-teach"><BookOpenCheck size={18} aria-hidden="true" />Teach your AI assistant</h2>
        <p className="vas-muted">Got a wrong or missing answer? Give the correct one; your AI assistant stores it and uses it in future replies.</p>
        <form className="vas-form" onSubmit={e => { e.preventDefault(); x.teach() }}>
          <Field label="Question or topic"><Input value={x.q} onChange={e => x.setQ(e.target.value)} placeholder="e.g. Do you deliver to Stellenbosch?" /></Field>
          <Field label="The correct answer"><Textarea rows={3} value={x.a} onChange={e => x.setA(e.target.value)} /></Field>
          <div className="vas-row">
            <Button type="submit" Icon={BookOpenCheck}>Teach it</Button>
            {x.msg && <span className={x.msg.startsWith('Learned') || x.msg === 'Teaching…' ? 'vas-ok' : 'vas-warn'} role="status">{plain(x.msg)}</span>}
          </div>
        </form>
      </Card>

      <section className="vas" aria-labelledby="vas-timeline">
        <div className="vas-row">
          <h2 className="vas-h vas-grow" id="vas-timeline">What your AI team has been doing</h2>
          <Button size="sm" variant="secondary" Icon={RefreshCw} onClick={x.load}>Refresh</Button>
        </div>
        {x.events === null ? <Card><Skeleton lines={4} /></Card>
          : x.events.length === 0
            ? <EmptyState Icon={Cpu} title="No activity yet">It appears here as customers and you chat with your AI assistant.</EmptyState>
            : <ul className="vas-events">
                {x.events.map((e, i) => <Event key={i} e={e} />)}
              </ul>}
      </section>
    </section>
  )
}

function Event({ e }) {
  const who = e.who === 'admin' ? 'you (owner)' : 'customer'
  if (e.kind === 'tool') {
    const Icon = e.consequential ? AlertTriangle : Wrench
    return (
      <li className="vas-event">
        <span className={`vas-dot${e.consequential ? ' vas-dot--warn' : ''}`} aria-hidden="true"><Icon size={16} /></span>
        <span className="vas-grow">
          {e.consequential && <span className="vas-sr">Consequential action (money, stock or a customer-facing change): </span>}
          {e.narrated || <b>{e.tool}</b>}<span className="vas-muted"> · {who}</span>
          {!e.narrated && e.args && Object.keys(e.args).length > 0 &&
            <span className="vas-muted"> · {Object.entries(e.args).map(([k, v]) => `${k}: ${v}`).join(', ').slice(0, 80)}</span>}
        </span>
        <span className="vas-muted vas-when">{ago(e.at)}</span>
      </li>)
  }
  return (
    <li className="vas-event">
      <span className={`vas-dot${e.escalated ? ' vas-dot--cloud' : ''}`} aria-hidden="true">{e.escalated ? <Cloud size={16} /> : <Cpu size={16} />}</span>
      <span className="vas-grow"><b>{e.escalated ? 'Cloud' : 'Local GPU'}</b><span className="vas-muted"> · {e.task}{e.reason ? ` · ${e.reason}` : ''}</span></span>
      <span className="vas-muted vas-when">{ago(e.at)}</span>
    </li>)
}
