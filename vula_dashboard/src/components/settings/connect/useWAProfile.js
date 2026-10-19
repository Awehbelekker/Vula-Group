/**
 * useWAProfile.js — the WhatsApp business profile, proposed from the brand kit, applied on one tap
 * (2026-10-19, UX redesign Phase F). Moved out of VulaWAProfile unchanged — same
 * GET/POST /admin/wa-profile (vula/commerce/wa_profile.py); nothing changes on WhatsApp before Apply.
 */
import { useEffect, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { toast } from '../../ui/index.jsx'

export const WA_PROFILE_ROWS = [
  ['about', 'About (the line under your name)', 139],
  ['description', 'Description', 512],
  ['email', 'Email', 128],
  ['address', 'Address', 256],
]

export function useWAProfile(tenantId) {
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

  const websites = String(form?.websites || '').split(/\s+/).filter(Boolean)
  return { data, form, setForm, busy, error, apply, websites }
}
