/**
 * SpecialsV2.jsx — Sell › Specials (24 Nov 2026): what the restaurant is running, in its own words.
 * Shown on the booking page and sent when a customer writes "specials" on WhatsApp — only the ones
 * that run that day. Prices typed in are read as written (lib/money randToCents).
 */
import { useCallback, useEffect, useState } from 'react'
import { ImagePlus, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'
import { api } from '../../lib/api'
import { uploadBrandImage } from '../../lib/brandUpload'
import { formatMoney, randToCents } from '../../lib/money'
import { Button, Card, EmptyState, ErrorCard, Field, Input, PageHeader, Sheet, Skeleton, StatusChip, Textarea, confirmDialog, toast } from '../ui/index.jsx'
import './tables.css'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const runs = (s) => (s.days?.length ? s.days.map(d => DAYS[d]).join(', ') : 'Every day')
  + (s.starts_on || s.ends_on ? ` · ${s.starts_on || 'now'} to ${s.ends_on || 'until you stop it'}` : '')

function Editor({ tenantId, special, onClose, onDone }) {
  const [f, setF] = useState({ title: special?.title || '', description: special?.description || '',
    price: special?.price_cents != null ? (special.price_cents / 100).toFixed(2) : '', image_url: special?.image_url || '',
    days: special?.days || [], starts_on: special?.starts_on || '', ends_on: special?.ends_on || '', active: special ? special.active !== false : true })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const photo = async (e) => {
    const file = e.target.files?.[0]; e.target.value = ''
    if (!file) return
    setBusy(true)
    const url = await uploadBrandImage(tenantId, file, 'specials')
    if (url) setF(x => ({ ...x, image_url: url })); else toast("The photo didn't upload. Try again.", 'danger')
    setBusy(false)
  }
  const save = async (e) => {
    e.preventDefault()
    const cents = f.price.trim() ? randToCents(f.price) : null
    if (f.price.trim() && (cents === null || cents < 0)) return setError('Enter the price like 65 or 65.00, or leave it empty.')
    setBusy(true); setError('')
    try {
      await api(`/v1/commerce/${tenantId}/admin/specials`, { method: 'POST', body: { ...(special ? { id: special.id } : {}), title: f.title,
        description: f.description, price_cents: cents, image_url: f.image_url, days: f.days, starts_on: f.starts_on || null, ends_on: f.ends_on || null, active: f.active } })
      toast('Saved'); onDone()
    } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <Sheet open title={special ? 'Change the special' : 'New special'} onClose={onClose}>
      <form className="vtb-form" onSubmit={save}>
        <Field label="What it is"><Input required maxLength={80} value={f.title} placeholder="Line fish and chips" onChange={e => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="A line about it (optional)"><Textarea maxLength={500} value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Price (R, optional)"><Input inputMode="decimal" value={f.price} placeholder="165.00" onChange={e => setF({ ...f, price: e.target.value })} /></Field>
        <div>
          {f.image_url && <img className="vtb-photo" src={f.image_url} alt="" />}
          <label className="vtb-upload"><ImagePlus size={16} aria-hidden="true" />{f.image_url ? 'Change the photo' : 'Add a photo'}<input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={busy} onChange={photo} /></label>
        </div>
        <span className="vtb-sub">Days it runs. None ticked means every day.</span>
        <div className="vtb-days" role="group" aria-label="Days the special runs">
          {DAYS.map((d, n) => <button key={d} type="button" aria-pressed={f.days.includes(n)} onClick={() => setF({ ...f, days: f.days.includes(n) ? f.days.filter(x => x !== n) : [...f.days, n].sort() })}>{d}</button>)}
        </div>
        <div className="vtb-row">
          <Field label="From (optional)"><Input type="date" value={f.starts_on} onChange={e => setF({ ...f, starts_on: e.target.value })} /></Field>
          <Field label="Until (optional)"><Input type="date" value={f.ends_on} onChange={e => setF({ ...f, ends_on: e.target.value })} /></Field>
        </div>
        <label className="vtb-check"><input type="checkbox" checked={f.active} onChange={e => setF({ ...f, active: e.target.checked })} /><span>Running</span></label>
        {error && <p className="vtb-error" role="alert">{error}</p>}
        <div><Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button></div>
      </form>
    </Sheet>
  )
}

export default function SpecialsV2({ tenantId }) {
  const [list, setList] = useState(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)       // null | 'new' | special
  const load = useCallback(() => {
    setError('')
    api(`/v1/commerce/${tenantId}/admin/specials`).then(d => setList(d.specials || [])).catch(e => setError(e.message))
  }, [tenantId])
  useEffect(() => { load() }, [load])
  const remove = async (s) => {
    if (!await confirmDialog(`Delete “${s.title}”?`)) return
    try { await api(`/v1/commerce/${tenantId}/admin/specials/${s.id}`, { method: 'DELETE' }); load() } catch (e) { toast(e.message, 'danger') }
  }
  return (
    <div className="vtb">
      <PageHeader sub="Your specials show on your booking page, and go to any customer who writes “specials” on WhatsApp — only the ones running that day."
        actions={<Button Icon={Plus} onClick={() => setEditing('new')}>New special</Button>} />
      {error ? <ErrorCard what="your specials" onRetry={load} /> : !list ? <Card><Skeleton lines={4} /></Card>
        : list.length === 0 ? <EmptyState Icon={Sparkles} title="No specials yet">Add what you’re running this week, with the days it’s on.</EmptyState>
        : <Card className="vtb-list"><ul>
            {list.map(s => (
              <li key={s.id} className={`vtb-b${s.active === false ? ' vtb-b--off' : ''}`}>
                {s.image_url ? <img className="vtb-thumb" src={s.image_url} alt="" loading="lazy" /> : <span className="vtb-thumb" aria-hidden="true" />}
                <span className="vtb-main"><strong>{s.title}{s.price_cents != null && <> · <span className="vula-money">{formatMoney(s.price_cents)}</span></>}</strong>
                  <span className="vtb-sub">{runs(s)}</span></span>
                <StatusChip tone={s.today ? 'ok' : 'muted'}>{s.active === false ? 'Stopped' : s.today ? 'On today' : 'Not today'}</StatusChip>
                <span className="vtb-actions"><Button size="sm" variant="secondary" Icon={Pencil} onClick={() => setEditing(s)}>Change</Button>
                  <Button size="sm" variant="quiet" Icon={Trash2} aria-label={`Delete ${s.title}`} onClick={() => remove(s)} /></span>
              </li>
            ))}
          </ul></Card>}
      {editing && <Editor tenantId={tenantId} special={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); load() }} />}
    </div>
  )
}
