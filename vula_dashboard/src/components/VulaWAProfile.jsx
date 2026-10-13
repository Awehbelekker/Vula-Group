/**
 * VulaWAProfile.jsx — the WhatsApp business profile, set from the brand kit (Settings › WhatsApp).
 * Backend: vula_mind/vula/commerce/wa_profile.py.
 *
 * What a customer sees when they tap the shop's name in WhatsApp — picture, about line,
 * description, email, website, address. Vula proposes it from what it already knows; the owner
 * checks or edits it and taps Apply. Nothing changes on WhatsApp before that tap.
 */
import { useEffect, useState } from 'react'
import { Button, Field, Input, Textarea, toast } from './ui/index.jsx'
import { VULA_API } from '../lib/authFetch'

const ROWS = [
  ['about', 'About (the line under your name)', 139],
  ['description', 'Description', 512],
  ['email', 'Email', 128],
  ['address', 'Address', 256],
]

export default function VulaWAProfile({ tenantId }) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/wa-profile`
  const [data, setData] = useState(null)
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(base).then(r => r.ok ? r.json() : Promise.reject())
      .then(d => { setData(d); setForm({ ...d.proposed, websites: (d.proposed.websites || []).join('\n') }) })
      .catch(() => setError('Could not load the WhatsApp profile.'))
  }, [base])

  async function apply() {
    setBusy(true); setError(null)
    try {
      const profile = { ...form, websites: String(form.websites || '').split(/\s+/).filter(Boolean) }
      const r = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile }) })
      const d = await r.json()
      if (!r.ok) { setError(d.detail || 'WhatsApp refused the update.'); return }
      setData(prev => ({ ...prev, current: d.profile }))
      toast(d.picture_note ? `Profile updated. ${d.picture_note}` : 'WhatsApp profile updated')
    } catch { setError('Could not reach Vula — nothing was changed.') } finally { setBusy(false) }
  }

  if (error && !data) return <p style={{ color: 'var(--danger)', fontSize: 13 }}>{error}</p>
  if (!data || !form) return <p style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</p>
  if (data.current?.error) return <p style={{ color: 'var(--muted)', fontSize: 13 }}>{data.current.error}</p>
  const cur = data.current || {}
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16, alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {ROWS.map(([key, label, max]) => (
          <Field key={key} label={`${label}${form[key] ? ` · ${form[key].length}/${max}` : ''}`}>
            {key === 'description'
              ? <Textarea rows={3} maxLength={max} value={form[key] || ''} onChange={e => setForm({ ...form, [key]: e.target.value })} />
              : <Input maxLength={max} value={form[key] || ''} onChange={e => setForm({ ...form, [key]: e.target.value })} />}
          </Field>
        ))}
        <Field label="Websites (up to 2)">
          <Textarea rows={2} value={form.websites} onChange={e => setForm({ ...form, websites: e.target.value })} />
        </Field>
        {form.picture_url && <p style={{ fontSize: 12, color: 'var(--muted)', margin: 0 }}>Profile picture: your brand icon / logo (square works best).</p>}
        {error && <p style={{ color: 'var(--danger)', fontSize: 13, margin: 0 }}>{error}</p>}
        <Button onClick={apply} disabled={busy}>{busy ? 'Updating…' : 'Apply to WhatsApp'}</Button>
      </div>
      {/* How it looks when a customer taps your name */}
      <div style={{ background: 'var(--surface-alt)', borderRadius: 14, padding: 14, textAlign: 'center' }}>
        {(form.picture_url || cur.profile_picture_url) && (
          <img src={form.picture_url || cur.profile_picture_url} alt="" style={{ width: 84, height: 84, borderRadius: '50%', objectFit: 'cover', background: 'var(--surface)' }} />
        )}
        <p style={{ margin: '8px 0 2px', fontWeight: 700, color: 'var(--text)' }}>Business account</p>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>{form.about || cur.about || ' '}</p>
        <div style={{ textAlign: 'left', fontSize: 12.5, marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6, color: 'var(--text)' }}>
          {form.description && <span>{form.description}</span>}
          {form.address && <span>📍 {form.address}</span>}
          {form.email && <span>✉️ {form.email}</span>}
          {String(form.websites || '').split(/\s+/).filter(Boolean).map(w => <span key={w}>🔗 {w}</span>)}
        </div>
        {cur.about && cur.about !== form.about && <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 12 }}>On WhatsApp now: “{cur.about}”</p>}
      </div>
    </div>
  )
}
