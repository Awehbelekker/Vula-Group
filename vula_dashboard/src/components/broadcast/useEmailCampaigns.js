/**
 * useEmailCampaigns.js — Marketing › Email, shared by VulaEmailCampaigns (current look) and
 * broadcast/EmailCampaignsV2 (the redesign, 2026-10-20). Moved verbatim: bulk email through the
 * connected mailbox to the same audiences and segments as WhatsApp broadcast, with the same
 * preview (dry run) → confirmed send; unsubscribed contacts are left out and every email carries
 * an unsubscribe link (server-side).
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'

const H = { 'Content-Type': 'application/json' }  // JWT attached by lib/authFetch

export const EMAIL_AUDIENCES = [
  { id: 'all', label: 'All customers' },
  { id: 'active_30d', label: 'Active (last 30 days)' },
  { id: 'high_value', label: 'High-value customers' },
]

export function useEmailCampaigns(tenantId) {
  const [campaigns, setCampaigns] = useState([])
  const [segments, setSegments] = useState([])
  const [loading, setLoading] = useState(true)
  const [subject, setSubject] = useState('')
  const [bodyText, setBodyText] = useState('')
  const [audiences, setAudiences] = useState(['all'])
  const toggleAud = (id) => setAudiences(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  const [testEmail, setTestEmail] = useState('')
  const [testMsg, setTestMsg] = useState('')
  const [sending, setSending] = useState(false)
  const [preview, setPreview] = useState(null)
  const [sent, setSent] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/email-campaigns`)
      const d = await r.json()
      setCampaigns(d.campaigns || [])
    } catch {}
    setLoading(false)
  }, [tenantId])

  const loadSegments = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/segments`)
      const d = await r.json()
      setSegments(d.segments || [])
    } catch {}
  }, [tenantId])

  useEffect(() => { load(); loadSegments() }, [load, loadSegments])

  async function sendTest() {
    if (!testEmail.trim() || !subject.trim() || !bodyText.trim()) return
    setTestMsg('')
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/email-campaigns/send`, {
      method: 'POST', headers: H,
      body: JSON.stringify({ subject, body: bodyText, test_email: testEmail, dry_run: false }),
    })
    const d = await r.json()
    setTestMsg(r.ok && d.sent ? `✓ Test sent to ${testEmail}` : (d.detail || 'Could not send test'))
  }

  async function previewCampaign() {
    if (!subject.trim() || !bodyText.trim()) { setError('Write a subject and message first.'); return }
    setSending(true); setError(null); setSent(false); setPreview(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/email-campaigns/send`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ subject, body: bodyText, audience_filter: audiences.join(',') || 'all', dry_run: true }),
      })
      const d = await r.json()
      if (r.ok) setPreview(d)
      else setError(d.detail || 'Preview failed')
    } catch (err) { setError(err.message) } finally { setSending(false) }
  }

  async function sendCampaign() {
    setSending(true); setError(null); setSent(false)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/email-campaigns/send`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ subject, body: bodyText, audience_filter: audiences.join(',') || 'all',
          name: subject, dry_run: false }),
      })
      const d = await r.json()
      if (r.ok) { setSent(d); setPreview(null); load() }
      else setError(d.detail || 'Send failed')
    } catch (err) { setError(err.message) } finally { setSending(false) }
  }

  const audLabel = (id) => EMAIL_AUDIENCES.find(a => a.id === id)?.label
    || ('🎯 ' + (segments.find(sg => `seg:${sg.id}` === id)?.name || 'segment'))
  const selectedAudLabel = audiences.length ? audiences.map(audLabel).join(' + ') : 'No audience selected'

  return { campaigns, segments, loading, subject, setSubject, bodyText, setBodyText, audiences, toggleAud, testEmail, setTestEmail, testMsg, sending, preview, setPreview, sent, error, sendTest, previewCampaign, sendCampaign, selectedAudLabel }
}
