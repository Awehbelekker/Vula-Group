/**
 * WAProfileV2.jsx — the redesigned "Your WhatsApp profile" in Settings (2026-10-19, UX redesign
 * Phase F). Same data and Apply as VulaWAProfile (connect/useWAProfile.js): the labelled fields
 * with their length limits, and beside them how the profile looks when a customer taps the name.
 */
import { Globe, Mail, MapPin, Send } from 'lucide-react'
import { Button, Card, Field, Input, Skeleton, Textarea } from '../../ui/index.jsx'
import { WA_PROFILE_ROWS, useWAProfile } from './useWAProfile'
import '../settings.css'

export default function WAProfileV2({ tenantId }) {
  const x = useWAProfile(tenantId)
  if (x.error && !x.data) return <p className="vcn-error" role="alert">{x.error}</p>
  if (!x.data || !x.form) return <Card><Skeleton lines={4} /></Card>
  if (x.data.current?.error) return <Card><p className="vst-body">{x.data.current.error}</p></Card>
  const f = x.form
  const cur = x.data.current || {}
  const set = (k, v) => x.setForm({ ...f, [k]: v })
  return (
    <div className="vcn-wa">
      <Card className="vst-card">
        {WA_PROFILE_ROWS.map(([key, label, max]) => (
          <Field key={key} label={label} hint={`${(f[key] || '').length}/${max}`}>
            {key === 'description'
              ? <Textarea rows={3} maxLength={max} value={f[key] || ''} onChange={e => set(key, e.target.value)} />
              : <Input maxLength={max} value={f[key] || ''} onChange={e => set(key, e.target.value)} />}
          </Field>
        ))}
        <Field label="Websites (up to 2)" hint="One per line.">
          <Textarea rows={2} value={f.websites} onChange={e => set('websites', e.target.value)} />
        </Field>
        {f.picture_url && <p className="vst-sub">Profile picture: your brand icon / logo (square works best).</p>}
        {x.error && <p className="vcn-error" role="alert">{x.error}</p>}
        <div className="vst-row"><Button Icon={Send} onClick={x.apply} disabled={x.busy}>{x.busy ? 'Updating…' : 'Apply to WhatsApp'}</Button></div>
      </Card>

      <figure className="vcn-wa-preview" aria-label="How your profile looks on WhatsApp">
        {(f.picture_url || cur.profile_picture_url) && <img src={f.picture_url || cur.profile_picture_url} alt="" className="vcn-wa-pic" />}
        <p className="vcn-wa-name">Business account</p>
        <p className="vst-sub">{f.about || cur.about || ' '}</p>
        <ul className="vcn-wa-lines">
          {f.description && <li>{f.description}</li>}
          {f.address && <li><MapPin size={16} aria-hidden="true" />{f.address}</li>}
          {f.email && <li><Mail size={16} aria-hidden="true" />{f.email}</li>}
          {x.websites.map(w => <li key={w}><Globe size={16} aria-hidden="true" />{w}</li>)}
        </ul>
        {cur.about && cur.about !== f.about && <figcaption className="vst-sub">On WhatsApp now: “{cur.about}”</figcaption>}
      </figure>
    </div>
  )
}
