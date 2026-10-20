/**
 * AutomationsV2.jsx — the redesigned Assistant › Automations (2026-10-20, UX redesign Phase F).
 * Same calls as VulaAutomations (assistant/useAutomations.js): standing trigger → action rules,
 * made by form or taught in plain language. Nothing sends by itself: a matched rule stages its
 * message here, and only "Approve & send" sends it. Delete asks first.
 */
import { Check, ClipboardCheck, PenLine, Plus, Send, Trash2, X, Zap } from 'lucide-react'
import { Button, Card, EmptyState, Field, IconButton, Input, Select, Skeleton, StatusChip, Textarea } from '../ui/index.jsx'
import { ACTIONS, ORDER_STATUSES, PLACEHOLDERS, TRIGGERS, useAutomations } from './useAutomations'
import './assistant.css'

const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+\s*/, '').replace(/\s*✓$/, '')

export default function AutomationsV2({ tenantId }) {
  const x = useAutomations(tenantId)
  const f = x.form
  const set = (k) => (e) => x.setForm(v => ({ ...v, [k]: e.target.value }))
  return (
    <section className="vas" aria-label="Automations">
      <p className="vas-sub">Standing rules: Vula checks every few minutes and, when one matches, stages the message for your approval. Nothing sends until you say go.</p>

      {x.firings.length > 0 && (
        <Card className="vas-card vas-approve" role="group" aria-labelledby="vas-firings">
          <h2 className="vas-h" id="vas-firings"><ClipboardCheck size={18} aria-hidden="true" />Waiting for your approval ({x.firings.length})</h2>
          <ul className="vas-list">
            {x.firings.map(fi => (
              <li key={fi.id} className="vas-item" aria-label={`Waiting: ${fi.message}`}>
                <span className="vas-grow"><span className="vas-body">{fi.message}</span><span className="vas-muted">{plain(ACTIONS[fi.action_type]?.label || fi.action_type)}</span></span>
                <Button size="sm" Icon={Send} onClick={() => x.decide(fi, 'approve')}>Approve & send</Button>
                <Button size="sm" variant="secondary" Icon={X} className="vas-danger" onClick={() => x.decide(fi, 'reject')}>Reject</Button>
              </li>))}
          </ul>
        </Card>)}

      <Card className="vas-card" role="group" aria-labelledby="vas-teachrule">
        <h2 className="vas-h" id="vas-teachrule"><PenLine size={18} aria-hidden="true" />Teach Vula a rule</h2>
        <p className="vas-muted">Describe it in plain language, e.g. "when an order is dispatched, message the customer to say it's on its way".</p>
        {x.showTeach ? (
          <form className="vas-form" onSubmit={e => { e.preventDefault(); x.teach() }}>
            <Field label="The rule"><Textarea rows={2} value={x.teachText} onChange={e => x.setTeachText(e.target.value)} placeholder="When… then…" /></Field>
            <div className="vas-row">
              <Button type="submit" Icon={Check} disabled={x.teaching}>{x.teaching ? 'Teaching…' : 'Create rule'}</Button>
              {x.teachMsg && <span className={x.teachMsg.includes('✓') ? 'vas-ok' : 'vas-warn'} role="status">{plain(x.teachMsg)}</span>}
            </div>
          </form>
        ) : <div><Button variant="secondary" Icon={PenLine} onClick={() => x.setShowTeach(true)}>Teach a rule</Button></div>}
      </Card>

      <div><Button Icon={x.showNew ? X : Plus} aria-expanded={x.showNew} onClick={() => x.setShowNew(v => !v)}>{x.showNew ? 'Close' : 'New automation'}</Button></div>
      {x.showNew && (
        <Card className="vas-card" role="group" aria-label="New automation">
          <form className="vas-form" onSubmit={e => { e.preventDefault(); x.create() }}>
            <Field label="Name" hint="Optional"><Input value={f.name} onChange={set('name')} /></Field>
            <div className="vas-grid">
              <Field label="When" hint={TRIGGERS[f.trigger_type].hint}>
                <Select value={f.trigger_type} onChange={set('trigger_type')}>
                  {Object.entries(TRIGGERS).map(([k, t]) => <option key={k} value={k}>{plain(t.label)}</option>)}
                </Select>
              </Field>
              {f.trigger_type === 'order_status' && (
                <Field label="Status">
                  <Select value={f.to_status} onChange={set('to_status')}>{ORDER_STATUSES.map(st => <option key={st} value={st}>{st}</option>)}</Select>
                </Field>)}
              <Field label="Then" hint={ACTIONS[f.action_type].hint}>
                <Select value={f.action_type} onChange={set('action_type')}>
                  {Object.entries(ACTIONS).map(([k, a]) => <option key={k} value={k}>{plain(a.label)}</option>)}
                </Select>
              </Field>
            </div>
            <Field label="Message to send" hint={`Placeholders you can use: ${PLACEHOLDERS[f.trigger_type]}`}>
              <Textarea rows={3} value={f.message} onChange={set('message')} />
            </Field>
            {x.error && <p className="vas-error" role="alert">{x.error}</p>}
            <div><Button type="submit" Icon={Check} disabled={x.saving}>{x.saving ? 'Saving…' : 'Create automation'}</Button></div>
          </form>
        </Card>)}

      {x.loading && !x.rows.length ? <Card><Skeleton lines={3} /></Card>
        : x.rows.length === 0
          ? <EmptyState Icon={Zap} title="No automations yet">Teach Vula a rule, or make one with the form.</EmptyState>
          : <ul className="vas-list vas-rules" aria-label="Automations">
              {x.rows.map(r => (
                <li key={r.id}>
                  <Card className="vas-row">
                    <span className="vas-grow"><b>{r.name}</b>
                      <span className="vas-muted">
                        {plain(TRIGGERS[r.trigger_type]?.label || r.trigger_type)}{r.trigger_config?.to_status ? ` → ${r.trigger_config.to_status}` : ''}
                        {' · '}{plain(ACTIONS[r.action_type]?.label || r.action_type)}
                        {r.fire_count > 0 && ` · matched ${r.fire_count}×`}
                        {r.created_from === 'conversation' && ' · taught'}
                      </span></span>
                    <button type="button" className="vas-switch" role="switch" aria-checked={!!r.enabled} aria-label={`${r.name} on`} onClick={() => x.toggle(r)}>
                      <StatusChip tone={r.enabled ? 'ok' : 'muted'}>{r.enabled ? 'On' : 'Off'}</StatusChip>
                    </button>
                    <IconButton quiet Icon={Trash2} className="vas-danger" label={`Delete ${r.name}`} onClick={() => x.remove(r)} />
                  </Card>
                </li>))}
            </ul>}
    </section>
  )
}
