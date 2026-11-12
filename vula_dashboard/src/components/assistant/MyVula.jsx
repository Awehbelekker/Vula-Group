/**
 * MyVula.jsx — "How Vula talks to you", under the dashboard chat. Each person sets their own
 * style within the company voice; nobody else sees or changes it. See assistant/useMyVula.js.
 */
import { Check, SlidersHorizontal } from 'lucide-react'
import { Button, Field, Input, Select } from '../ui/index.jsx'
import { useMyVula } from './useMyVula'
import './assistant.css'

function Choice({ label, value, onChange, options, blank }) {
  return (
    <Field label={label}>
      <Select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">{blank}</option>
        {options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
      </Select>
    </Field>
  )
}

export default function MyVula({ tenantId }) {
  const x = useMyVula(tenantId)
  if (!x.known) return null
  const failed = x.status && x.status !== 'saving' && x.status !== 'saved'
  return (
    <details className="vas-mine">
      <summary className="vas-mine-sum"><SlidersHorizontal size={16} aria-hidden="true" />How Vula talks to you</summary>
      <p className="vas-muted">Your own preferences, on WhatsApp and here. They change how Vula words things for you, never the facts. Only you can see or change them.</p>
      <form className="vas-mine-grid" onSubmit={e => { e.preventDefault(); x.save() }}>
        <Choice label="Style" value={x.prefs.style} onChange={v => x.set('style', v)}
          options={x.options.style} blank="Company voice" />
        <Choice label="Reply length" value={x.prefs.reply_length} onChange={v => x.set('reply_length', v)}
          options={x.options.reply_length} blank="Normal" />
        <Choice label="Language" value={x.prefs.language} onChange={v => x.set('language', v)}
          options={x.options.language} blank="Match what I write" />
        <Field label="What to call you">
          <Input value={x.prefs.nickname} maxLength={30} onChange={e => x.set('nickname', e.target.value)}
            placeholder="e.g. Thandi" />
        </Field>
        <div className="vas-row">
          <Button type="submit" Icon={Check} disabled={x.status === 'saving'}>{x.status === 'saving' ? 'Saving…' : 'Save'}</Button>
          {x.status === 'saved' && <span className="vas-ok" role="status">Saved</span>}
          {failed && <span className="vas-warn" role="alert">{x.status}</span>}
        </div>
      </form>
    </details>
  )
}
