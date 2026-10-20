/**
 * FlowBuilderV2.jsx — the redesigned Assistant › Flows (2026-10-20, UX redesign Phase F). Same calls
 * as VulaFlowBuilder (assistant/useFlowBuilder.js): a customer texts a trigger phrase, Vula asks the
 * steps in order (free text / yes-no / multiple choice, with keyword branches), then runs an end
 * action. A form, not a canvas: each step is a numbered card with labelled fields. Delete asks first.
 */
import { Check, GitBranch, Plus, Trash2, Workflow, X } from 'lucide-react'
import { Button, Card, EmptyState, Field, IconButton, Input, Select, Skeleton, StatusChip, Textarea } from '../ui/index.jsx'
import { END_ACTIONS, REPLY_TYPES, useFlowBuilder } from './useFlowBuilder'
import './assistant.css'

const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+\s*/, '')

export default function FlowBuilderV2({ tenantId }) {
  const x = useFlowBuilder(tenantId)
  const f = x.form
  const set = (k) => (e) => x.setForm(v => ({ ...v, [k]: e.target.value }))
  return (
    <section className="vas" aria-label="Flows">
      <p className="vas-sub">Build a guided conversation: a customer texts a trigger phrase, Vula asks your questions in order, then runs an action at the end. No code, just fill in the steps.</p>
      <div><Button Icon={x.showNew ? X : Plus} aria-expanded={x.showNew} onClick={() => x.setShowNew(v => !v)}>{x.showNew ? 'Close' : 'New flow'}</Button></div>

      {x.showNew && (
        <Card className="vas-card" role="group" aria-label="New flow">
          <form className="vas-form" onSubmit={e => { e.preventDefault(); x.create() }}>
            <Field label="Flow name"><Input value={f.name} onChange={set('name')} placeholder="e.g. Book a demo" /></Field>
            <Field label="Trigger phrases" hint={'Comma-separated. "hi", "menu" and a few other reserved words always take priority.'}>
              <Input value={f.trigger_phrases} onChange={set('trigger_phrases')} placeholder="e.g. book a demo, schedule a demo" />
            </Field>
            <h3 className="vas-h4">Steps</h3>
            <ol className="vas-steps">
              {f.steps.map((st, i) => (
                <li key={i} className="vas-stepcard" role="group" aria-label={`Step ${i + 1}`}>
                  <div className="vas-row">
                    <b className="vas-grow">Step {i + 1}</b>
                    {f.steps.length > 1 && <IconButton quiet Icon={Trash2} className="vas-danger" label={`Remove step ${i + 1}`} onClick={() => x.removeStep(i)} />}
                  </div>
                  <Field label="What should Vula ask?"><Textarea rows={2} value={st.prompt} onChange={e => x.updateStep(i, { prompt: e.target.value })} /></Field>
                  <div className="vas-grid">
                    <Field label="Reply type" hint={REPLY_TYPES[st.reply_type].hint}>
                      <Select value={st.reply_type} onChange={e => x.updateStep(i, { reply_type: e.target.value })}>
                        {Object.entries(REPLY_TYPES).map(([k, t]) => <option key={k} value={k}>{plain(t.label)}</option>)}
                      </Select>
                    </Field>
                    <Field label="Save the answer as" hint="e.g. location"><Input value={st.save_as} onChange={e => x.updateStep(i, { save_as: e.target.value })} /></Field>
                  </div>
                  {st.reply_type === 'multiple_choice' && (
                    <Field label="Options" hint="Comma-separated, e.g. Cape Town, Durban, Joburg">
                      <Input value={st.options} onChange={e => x.updateStep(i, { options: e.target.value })} />
                    </Field>)}
                  {st.reply_type !== 'free_text' && (
                    <Field label="Branch rules" hint={'One per line: a keyword in the reply → a step number, or "end" to finish there.'}>
                      <Textarea rows={2} className="vas-mono" value={st.branches} onChange={e => x.updateStep(i, { branches: e.target.value })} placeholder={'cape town: 2\ndurban: 3'} />
                    </Field>)}
                  <Field label="If nothing matches">
                    <Select value={st.default_next} onChange={e => x.updateStep(i, { default_next: e.target.value })}>
                      <option value="">Next step</option>
                      {Array.from({ length: x.total }, (_, n) => n + 1).filter(n => n !== i + 1).map(n => <option key={n} value={String(n)}>Go to step {n}</option>)}
                      <option value="end">End the flow (run the action)</option>
                    </Select>
                  </Field>
                </li>))}
            </ol>
            <div><Button type="button" variant="secondary" size="sm" Icon={Plus} onClick={x.addStep}>Add step</Button></div>

            <h3 className="vas-h4">When the flow finishes</h3>
            <Field label="Action" hint={`${END_ACTIONS[f.end_action_tool].hint} Use {{save_as key}} to reference an answer, or {{__phone}} / {{__customer_name}}.`}>
              <Select value={f.end_action_tool} onChange={set('end_action_tool')}>
                {Object.entries(END_ACTIONS).map(([k, a]) => <option key={k} value={k}>{plain(a.label)}</option>)}
              </Select>
            </Field>
            <Field label="Action details" hint="One per line, e.g. start: {{date}}T09:00">
              <Textarea rows={3} className="vas-mono" value={f.end_action_args} onChange={set('end_action_args')} placeholder={'start: {{date}}T09:00\ncustomer_phone: {{__phone}}'} />
            </Field>
            <Field label="Closing message to the customer" hint="Optional"><Textarea rows={2} value={f.closing_message} onChange={set('closing_message')} /></Field>
            {x.error && <p className="vas-error" role="alert">{x.error}</p>}
            <div><Button type="submit" Icon={Check} disabled={x.saving}>{x.saving ? 'Saving…' : 'Create flow'}</Button></div>
          </form>
        </Card>)}

      {x.loading && !x.rows.length ? <Card><Skeleton lines={3} /></Card>
        : x.rows.length === 0
          ? <EmptyState Icon={Workflow} title="No flows yet">Make one above: a trigger phrase, a few questions and an action.</EmptyState>
          : <ul className="vas-list vas-rules" aria-label="Flows">
              {x.rows.map(r => (
                <li key={r.id}>
                  <Card className="vas-row">
                    <span className="vas-dot" aria-hidden="true"><GitBranch size={16} /></span>
                    <span className="vas-grow"><b>{r.name}</b>
                      <span className="vas-muted">"{(r.trigger_phrases || []).join('", "')}" · {(r.steps || []).length} step{(r.steps || []).length === 1 ? '' : 's'}
                        {r.fire_count > 0 && ` · completed ${r.fire_count}×`}</span></span>
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
