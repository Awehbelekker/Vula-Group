/**
 * WATemplatesV2.jsx — the redesigned Marketing › Templates (2026-10-20, UX redesign Phase F). Same
 * calls as VulaWATemplates (marketing/useWATemplates.js): the WhatsApp templates Meta has
 * approved / is reviewing / rejected, submitting the standard set Vula's own reminders need,
 * deleting one (asks first), and a new-template sheet with labelled fields.
 */
import { Link2, MessagesSquare, MousePointerClick, Plus, Send, Trash2, X } from 'lucide-react'
import { SA_TZ } from '../../lib/dates'
import { Button, DataTable, EmptyState, Field, IconButton, Input, Select, Sheet, StatusChip, Textarea } from '../ui/index.jsx'
import { useTemplateForm, useWATemplates } from './useWATemplates'
import './marketing.css'

const STATUS = { APPROVED: ['ok', 'Approved'], PENDING: ['warn', 'Pending review'], REJECTED: ['danger', 'Rejected'] }
const chip = (r) => { const [tone, label] = STATUS[r.status] || ['muted', 'Unknown']; return <StatusChip tone={tone}>{label}</StatusChip> }
const day = (iso) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric', timeZone: SA_TZ })
}
const media = (r) => [r.header_type && `${r.header_type.toLowerCase()} header`, ...(r.buttons || []).map(b => `button: ${b.text}`)].filter(Boolean).join(' · ') || '—'

export default function WATemplatesV2({ tenantId }) {
  const x = useWATemplates(tenantId)
  return (
    <section className="vmk" aria-label="WhatsApp templates">
      <div className="vmk-head">
        <p className="vmk-sub">Needed to message someone who hasn't messaged you lately. Meta reviews each one, usually within minutes to a day.</p>
        <div className="vmk-row">
          <Button variant="secondary" Icon={Send} disabled={x.busy} onClick={x.submitStandard}
            title="Submit the templates Vula's own reminders and summaries need, named for this business">Submit standard templates</Button>
          <Button Icon={Plus} onClick={() => x.setAdding(true)}>New template</Button>
        </div>
      </div>
      {x.msg && <p className="vmk-note" role="status">{x.msg}</p>}

      {!x.busy && !x.rows.length
        ? <EmptyState Icon={MessagesSquare} title="No templates yet">Create one for any message that needs to reach someone cold.</EmptyState>
        : <DataTable caption="WhatsApp templates" rows={x.rows} rowKey="name" loading={x.busy && !x.rows.length}
            columns={[
              { key: 'name', label: 'Name', render: r => <span className="vmk-mono">{r.name}</span> },
              { key: 'category', label: 'Category', render: r => r.category?.toLowerCase() },
              { key: 'body_text', label: 'Body', render: r => <span className="vmk-body-cell">{r.body_text}</span> },
              { key: 'media', label: 'Image / buttons', render: r => <span className="vmk-muted">{media(r)}</span> },
              { key: 'status', label: 'Status', render: r => <span title={r.rejected_reason || undefined}>{chip(r)}</span> },
              { key: 'created_at', label: 'Created', render: r => day(r.created_at) },
              { key: 'del', label: '', align: 'right', render: r => (
                <IconButton quiet Icon={Trash2} className="vmk-danger" label={`Delete template ${r.name}`} onClick={() => x.remove(r.name)} />) },
            ]}
            stack={{
              title: r => r.name,
              detail: r => `${r.category?.toLowerCase() || ''} · ${r.body_text}`,
              trailing: r => <span className="vmk-trail">{chip(r)}
                <IconButton quiet Icon={Trash2} className="vmk-danger" label={`Delete template ${r.name}`} onClick={() => x.remove(r.name)} /></span>,
            }} />}

      <p className="vmk-muted">Use <code>{'{{1}}'}</code>, <code>{'{{2}}'}</code>… in the body for the parts that change each send (name, order number, amount). Plain, non-promotional wording is approved faster unless it is a marketing message.</p>

      {x.adding && <NewTemplate tenantId={tenantId} flash={x.flash}
        onClose={() => x.setAdding(false)}
        onDone={() => { x.setAdding(false); x.load(); x.flash('Submitted. Meta is reviewing it.') }} />}
    </section>
  )
}

function NewTemplate({ tenantId, flash, onClose, onDone }) {
  const f = useTemplateForm(tenantId, { onDone, flash })
  return (
    <Sheet open title="New WhatsApp template" onClose={onClose}
      footer={<div className="vmk-row">
        <Button type="submit" form="vmk-tpl" Icon={Send} disabled={f.busy}>{f.busy ? 'Submitting…' : 'Submit to Meta for review'}</Button>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
      </div>}>
      <form id="vmk-tpl" className="vmk-form" onSubmit={e => { e.preventDefault(); f.submit() }}>
        <Field label="Name" hint="lowercase_with_underscores, e.g. order_ready">
          <Input className="vmk-mono" value={f.name} onChange={e => f.setName(e.target.value)} autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label="Category">
          <Select value={f.category} onChange={e => f.setCategory(e.target.value)}>
            <option value="UTILITY">Utility (order / account updates)</option>
            <option value="MARKETING">Marketing (promotions, campaigns)</option>
          </Select>
        </Field>
        <Field label="Message" hint="Use {{1}}, {{2}}… for the parts that change each send.">
          <Textarea rows={4} value={f.bodyText} onChange={e => f.setBodyText(e.target.value)} placeholder="Hi {{1}}, your order {{2}} is ready. Reply if you have any questions." />
        </Field>
        {f.paramCount > 0 && (
          <Field label={`Example values for {{1}}…{{${f.paramCount}}}`} hint="Comma-separated, e.g. Sarah, OFF-00042. Meta uses them to review it.">
            <Input value={f.examples} onChange={e => f.setExamples(e.target.value)} />
          </Field>)}
        <Field label="Header">
          <Select value={f.headerType} onChange={e => f.setHeaderType(e.target.value)}>
            <option value="">No header image</option>
            <option value="IMAGE">Image header</option>
            <option value="VIDEO">Video header</option>
          </Select>
        </Field>
        {f.headerType && (
          <Field label="Sample image or video for Meta to review" hint="A public URL.">
            <Input type="url" value={f.headerUrl} onChange={e => f.setHeaderUrl(e.target.value)} placeholder="https://…" />
          </Field>)}
        <fieldset className="vmk-fieldset">
          <legend className="vmk-legend">Buttons <span className="vmk-muted">Up to two.</span></legend>
          {f.buttons.map((b, i) => (
            <div key={i} className="vmk-button" role="group" aria-label={`Button ${i + 1}`}>
              <Field label="Kind">
                <Select value={b.type} onChange={e => f.updButton(i, { type: e.target.value })}>
                  <option value="URL">Link button</option>
                  <option value="QUICK_REPLY">Quick reply</option>
                </Select>
              </Field>
              <Field label="Button text"><Input value={b.text} onChange={e => f.updButton(i, { text: e.target.value })} placeholder="e.g. Shop now" /></Field>
              {b.type === 'URL' && (
                <Field label="Link" hint="…/l/{{1}} for a click-tracked link">
                  <Input type="url" value={b.url} onChange={e => f.updButton(i, { url: e.target.value })} placeholder="https://yoursite.com/…" />
                </Field>)}
              <IconButton quiet Icon={X} className="vmk-danger" label={`Remove button ${i + 1}`} onClick={() => f.dropButton(i)} />
            </div>))}
          {f.buttons.length < 2 && <div><Button variant="secondary" size="sm" Icon={f.buttons.length ? MousePointerClick : Link2} type="button" onClick={f.addButton}>Add button</Button></div>}
        </fieldset>
      </form>
    </Sheet>
  )
}
