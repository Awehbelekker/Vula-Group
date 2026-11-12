/**
 * MyVula.jsx — what is personal to the signed-in person, under the dashboard chat: "What Vula
 * remembers about you" (their private notes) and "How Vula talks to you" (their own style within
 * the company voice). Nobody else sees or changes either. See assistant/useMyVula.js.
 */
import { Check, Lock, Plus, SlidersHorizontal, Trash2 } from 'lucide-react'
import { Button, Field, IconButton, Input, Select, confirmDialog } from '../ui/index.jsx'
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

function Notes({ x }) {
  const busy = x.memoryStatus === 'working'
  const failed = x.memoryStatus && !busy
  const forgetAll = async () => {
    const yes = await confirmDialog("Forget everything Vula is keeping about you? This can't be undone.",
      { confirmLabel: 'Forget everything', danger: true })
    if (yes) x.forgetAll()
  }
  return (
    <details className="vas-mine">
      <summary className="vas-mine-sum"><Lock size={16} aria-hidden="true" />What Vula remembers about you</summary>
      <p className="vas-muted">Things you asked Vula to remember about you. Nobody else at the business can see them, and Vula only keeps what you ask it to. You can also say "remember that I…" in the chat or on WhatsApp.</p>
      {x.notes.length === 0
        ? <p className="vas-muted">Vula isn't keeping anything about you.</p>
        : (
          <ul className="vas-notes" aria-label="Your private notes">
            {x.notes.map(n => (
              <li key={n.id} className="vas-note">
                <span className="vas-body">{n.text}</span>
                <IconButton Icon={Trash2} label={`Forget: ${n.text}`} quiet disabled={busy} onClick={() => x.forgetNote(n.id)} />
              </li>))}
          </ul>)}
      <form className="vas-row" onSubmit={e => { e.preventDefault(); x.addNote() }}>
        <Input className="vas-note-input" value={x.draft} maxLength={300} onChange={e => x.setDraft(e.target.value)}
          aria-label="Something for Vula to remember about you" placeholder="e.g. I prefer the numbers first, then the detail" />
        <Button type="submit" Icon={Plus} disabled={busy || !x.draft.trim()}>Remember this</Button>
      </form>
      <div className="vas-row">
        {x.notes.length > 0 && <Button size="sm" variant="secondary" Icon={Trash2} disabled={busy} onClick={forgetAll}>Forget everything</Button>}
        {failed && <span className="vas-warn" role="alert">{x.memoryStatus}</span>}
      </div>
    </details>
  )
}

function Style({ x }) {
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

export default function MyVula({ tenantId }) {
  const x = useMyVula(tenantId)
  return (
    <>
      {x.memoryOn && <Notes x={x} />}
      {x.known && <Style x={x} />}
    </>
  )
}
