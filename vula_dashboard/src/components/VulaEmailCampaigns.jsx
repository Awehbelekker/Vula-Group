/**
 * VulaEmailCampaigns.jsx — bulk/campaign email, distinct from the 1:1 business
 * correspondence in the Assistant's email skill. Reuses the exact same audience
 * targeting (built-in filters + saved segments) as WhatsApp Broadcast — a customer's
 * email is already merged into the same aggregated customer list.
 */
import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
const API_KEY = import.meta.env.VITE_API_KEY || ''
const H = { 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) }

const AUDIENCES = [
  { id: 'all', label: 'All customers' },
  { id: 'active_30d', label: 'Active (last 30 days)' },
  { id: 'high_value', label: 'High-value customers' },
]

export default function VulaEmailCampaigns({ tenantId }) {
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

  const audLabel = (id) => AUDIENCES.find(a => a.id === id)?.label
    || ('🎯 ' + (segments.find(sg => `seg:${sg.id}` === id)?.name || 'segment'))
  const selectedAudLabel = audiences.length ? audiences.map(audLabel).join(' + ') : 'No audience selected'

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>✉️ Email Campaigns</h3>
        <p style={s.sub}>Send a one-off email to a segment of customers, via your connected mailbox. Every email includes an unsubscribe link.</p>
      </div>

      <div style={s.composeCard}>
        <p style={s.sectionLabel}>Subject</p>
        <input value={subject} onChange={e => setSubject(e.target.value)}
          placeholder="e.g. This week's specials" style={{ ...s.input, width: '100%', boxSizing: 'border-box' }} />

        <p style={s.sectionLabel}>Message</p>
        <textarea value={bodyText} onChange={e => setBodyText(e.target.value)}
          placeholder="Plain text — this is exactly what customers receive (plus an unsubscribe footer)."
          style={s.textarea} rows={8} />

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
          <input value={testEmail} onChange={e => setTestEmail(e.target.value)} placeholder="Test to: you@example.com"
            style={{ ...s.input, width: 220 }} />
          <button onClick={sendTest} style={{ ...s.aiBtn, background: '#5B6B7A' }}>📧 Send test</button>
          {testMsg && <span style={{ fontSize: 12, fontFamily: 'system-ui', color: testMsg.startsWith('✓') ? '#16a34a' : '#ef4444' }}>{testMsg}</span>}
        </div>

        <p style={s.sectionLabel}>Audience <span style={{ fontWeight: 400, color: '#8A8680' }}>— pick one or more; overlaps de-duplicated</span></p>
        <div style={s.audRow}>
          {AUDIENCES.map(a => (
            <button key={a.id} onClick={() => toggleAud(a.id)} style={{ ...s.audBtn, ...(audiences.includes(a.id) ? s.audBtnActive : {}) }}>
              {audiences.includes(a.id) ? '☑ ' : '☐ '}{a.label}
            </button>
          ))}
          {segments.map(seg => (
            <button key={seg.id} onClick={() => toggleAud(`seg:${seg.id}`)} style={{ ...s.audBtn, ...(audiences.includes(`seg:${seg.id}`) ? s.audBtnActive : {}) }}>
              {audiences.includes(`seg:${seg.id}`) ? '☑ ' : '☐ '}🎯 {seg.name}
            </button>
          ))}
        </div>

        {error && <p style={s.error}>{error}</p>}
        {sent && (
          <p style={s.success}>
            ✓ Sent to {sent.sent} recipient{sent.sent !== 1 ? 's' : ''}
            {sent.failed ? ` · ${sent.failed} failed` : ''}.
          </p>
        )}

        <div style={s.preview}>
          <p style={s.previewLabel}>About to send</p>
          <p style={s.previewText}><strong>{selectedAudLabel}</strong></p>
        </div>

        {!preview ? (
          <button onClick={previewCampaign} disabled={sending} style={sending ? s.btnDisabled : s.sendBtn}>
            {sending ? 'Checking…' : '👁 Preview audience'}
          </button>
        ) : (
          <div style={s.confirmBox}>
            <p style={s.confirmText}>
              This will email <strong>{preview.recipient_count}</strong> customer{preview.recipient_count !== 1 ? 's' : ''}
              {preview.sample?.length > 0 && (
                <span style={s.sampleNames}> — e.g. {preview.sample.slice(0, 3).map(r => r.email).join(', ')}{preview.recipient_count > 3 ? '…' : ''}</span>
              )}
            </p>
            {preview.suppressed_count > 0 && (
              <p style={s.sampleNames}>🚫 {preview.suppressed_count} unsubscribed contact{preview.suppressed_count !== 1 ? 's' : ''} excluded</p>
            )}
            {preview.recipient_count === 0 ? (
              <p style={s.error}>No recipients with an email address in this segment — nothing to send.</p>
            ) : (
              <div style={s.confirmRow}>
                <button onClick={() => setPreview(null)} style={s.cancelBtn}>Cancel</button>
                <button onClick={sendCampaign} disabled={sending} style={sending ? s.btnDisabled : s.sendLiveBtn}>
                  {sending ? 'Sending…' : `✉️ Send live to ${preview.recipient_count}`}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <p style={s.sectionLabel}>Campaign history</p>
      {loading ? <p style={s.muted}>Loading…</p> : campaigns.length === 0 ? (
        <p style={s.muted}>No email campaigns yet. Create your first one above.</p>
      ) : (
        <div style={s.histList}>
          {campaigns.map(c => (
            <div key={c.id} style={s.histRow}>
              <div style={{ flex: 1 }}>
                <span style={s.histName}>{c.name || c.subject}</span>
                <span style={s.histMeta}>{c.audience_filter} · {c.status} · {new Date(c.created_at).toLocaleDateString('en-ZA')}</span>
              </div>
              <div style={s.histStats}>
                {c.sent_count > 0 && <span style={s.statChip}>✉ {c.sent_count}</span>}
                {c.failed_count > 0 && <span style={{ ...s.statChip, color: '#A23B2D' }}>⚠ {c.failed_count}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: '0 0 4px' },
  sub:          { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0, lineHeight: 1.5 },
  composeCard:  { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 10, padding: 18, marginBottom: 20 },
  sectionLabel: { fontFamily: 'system-ui', fontSize: 12, fontWeight: 600, color: '#1E1E1E', margin: '16px 0 8px' },
  input:        { padding: '10px 12px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 14, fontFamily: 'system-ui', outline: 'none', boxSizing: 'border-box' },
  textarea:     { width: '100%', padding: '10px 12px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 14, fontFamily: 'system-ui', outline: 'none', boxSizing: 'border-box', resize: 'vertical' },
  aiBtn:        { padding: '10px 16px', background: '#C4861A', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', whiteSpace: 'nowrap' },
  audRow:       { display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 },
  audBtn:       { background: '#F7F4EE', border: '1px solid #DDD8CE', borderRadius: 20, padding: '6px 12px', cursor: 'pointer', textAlign: 'left', fontSize: 12.5, fontFamily: 'system-ui', color: '#1E1E1E' },
  audBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, var(--accent))' },
  preview:      { background: '#F7F4EE', borderRadius: 6, padding: '10px 14px', marginBottom: 14 },
  previewLabel: { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', margin: '0 0 4px' },
  previewText:  { fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E', margin: 0 },
  error:        { color: '#ef4444', fontSize: 13, fontFamily: 'system-ui', margin: '0 0 10px' },
  success:      { color: '#16a34a', fontSize: 13, fontFamily: 'system-ui', margin: '0 0 10px', fontWeight: 600 },
  sendBtn:      { width: '100%', padding: '12px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  btnDisabled:  { width: '100%', padding: '12px', background: '#DDD8CE', color: '#8A8680', border: 'none', borderRadius: 8, fontSize: 14, cursor: 'not-allowed', fontFamily: 'system-ui' },
  confirmBox:   { background: 'rgba(37,211,102,0.06)', border: '1px solid rgba(37,211,102,0.3)', borderRadius: 8, padding: 14 },
  confirmText:  { fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E', margin: '0 0 10px', lineHeight: 1.5 },
  sampleNames:  { color: '#8A8680' },
  confirmRow:   { display: 'flex', gap: 8 },
  cancelBtn:    { padding: '10px 16px', background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  sendLiveBtn:  { flex: 1, padding: '10px 16px', background: '#16a34a', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  muted:        { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  histList:     { display: 'flex', flexDirection: 'column', gap: 6 },
  histRow:      { display: 'flex', alignItems: 'center', gap: 10, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 14px' },
  histName:     { display: 'block', fontFamily: 'system-ui', fontSize: 13.5, fontWeight: 600, color: '#1E1E1E' },
  histMeta:     { display: 'block', fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  histStats:    { display: 'flex', gap: 6, alignItems: 'center' },
  statChip:     { fontFamily: 'monospace', fontSize: 11.5, color: '#8A8680', background: '#F7F4EE', padding: '2px 7px', borderRadius: 10 },
}
