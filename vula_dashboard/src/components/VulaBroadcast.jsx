/**
 * VulaBroadcast.jsx — WhatsApp broadcast / campaign builder.
 *
 * Send promotional messages to customers via approved Meta templates.
 * Audience filters: all / ordered last 30 days / high value (>R500).
 * History shows sent/delivered/read counts.
 */

import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL ?? '/api'

const TEMPLATES = [
  { id: 'oth_weekly_fish',        label: '🐟 Weekly fish specials',         hint: 'This week\'s fresh catch + prices' },
  { id: 'oth_new_stock',          label: '📦 New stock arrived',             hint: 'Fresh delivery announcement' },
  { id: 'oth_reorder_reminder',   label: '🔔 Reorder reminder',             hint: 'Remind customers who haven\'t ordered in 2+ weeks' },
  { id: 'oth_seasonal_promo',     label: '🎉 Seasonal promotion',            hint: 'Special discount or limited-time offer' },
  { id: 'oth_delivery_update',    label: '🚚 Delivery slot reminder',        hint: 'Remind customers about delivery days' },
]

const AUDIENCES = [
  { id: 'all',          label: 'All customers',            hint: 'Everyone who has ever ordered' },
  { id: 'ordered_30d',  label: 'Active (last 30 days)',    hint: 'Customers who ordered recently' },
  { id: 'high_value',   label: 'High-value customers',    hint: 'Customers with total orders > R500' },
]

export default function VulaBroadcast({ tenantId }) {
  const [broadcasts, setBroadcasts] = useState([])
  const [loading, setLoading] = useState(true)
  const [template, setTemplate] = useState(TEMPLATES[0].id)
  const [audience, setAudience] = useState('all')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(null)       // live-send result {sent, failed, recipient_count}
  const [preview, setPreview] = useState(null)  // dry-run result {recipient_count, sample}
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts`)
      const d = await r.json()
      setBroadcasts(d.broadcasts || [])
    } catch {}
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  // Step 1 — preview (dry-run): who would this reach? No messages sent.
  async function previewBroadcast() {
    setSending(true); setError(null); setSent(false); setPreview(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/send`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: template, audience_filter: audience, dry_run: true }),
      })
      const d = await r.json()
      if (r.ok) setPreview(d)
      else setError(d.detail || 'Preview failed')
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  // Step 2 — confirmed live send.
  async function sendBroadcast() {
    setSending(true); setError(null); setSent(false)
    try {
      const tpl = TEMPLATES.find(t => t.id === template)
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/send`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: template, audience_filter: audience, name: tpl?.label, dry_run: false }),
      })
      const d = await r.json()
      if (r.ok) { setSent(d); setPreview(null); load() }
      else setError(d.detail || 'Send failed')
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  const selectedTpl = TEMPLATES.find(t => t.id === template)
  const selectedAud = AUDIENCES.find(a => a.id === audience)

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>📢 WhatsApp Broadcasts</h3>
        <p style={s.sub}>Send promotional messages to your customers via approved WhatsApp templates.</p>
      </div>

      {/* Compose */}
      <div style={s.composeCard}>
        <p style={s.sectionLabel}>Message template</p>
        <div style={s.tplList}>
          {TEMPLATES.map(t => (
            <button
              key={t.id}
              onClick={() => setTemplate(t.id)}
              style={{ ...s.tplBtn, ...(template === t.id ? s.tplBtnActive : {}) }}
            >
              <span style={s.tplLabel}>{t.label}</span>
              <span style={s.tplHint}>{t.hint}</span>
            </button>
          ))}
        </div>

        <p style={s.sectionLabel}>Audience</p>
        <div style={s.audRow}>
          {AUDIENCES.map(a => (
            <button
              key={a.id}
              onClick={() => setAudience(a.id)}
              style={{ ...s.audBtn, ...(audience === a.id ? s.audBtnActive : {}) }}
            >
              <span style={s.audLabel}>{a.label}</span>
              <span style={s.audHint}>{a.hint}</span>
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
          <p style={s.previewLabel}>About to broadcast</p>
          <p style={s.previewText}>
            <strong>{selectedTpl?.label}</strong> → <strong>{selectedAud?.label}</strong>
          </p>
        </div>

        {/* Step 1: preview the audience (no send). Step 2: confirm live send. */}
        {!preview ? (
          <button onClick={previewBroadcast} disabled={sending} style={sending ? s.btnDisabled : s.sendBtn}>
            {sending ? 'Checking…' : '👁 Preview audience'}
          </button>
        ) : (
          <div style={s.confirmBox}>
            <p style={s.confirmText}>
              This will send <strong>{selectedTpl?.label}</strong> to{' '}
              <strong>{preview.recipient_count}</strong> customer{preview.recipient_count !== 1 ? 's' : ''}
              {preview.sample?.length > 0 && (
                <span style={s.sampleNames}>
                  {' '}— e.g. {preview.sample.slice(0, 3).map(r => r.name).join(', ')}
                  {preview.recipient_count > 3 ? '…' : ''}
                </span>
              )}
            </p>
            {preview.recipient_count === 0 ? (
              <p style={s.error}>No recipients in this segment — nothing to send.</p>
            ) : (
              <div style={s.confirmRow}>
                <button onClick={() => setPreview(null)} style={s.cancelBtn}>Cancel</button>
                <button onClick={sendBroadcast} disabled={sending} style={sending ? s.btnDisabled : s.sendLiveBtn}>
                  {sending ? 'Sending…' : `💬 Send live to ${preview.recipient_count}`}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* History */}
      <p style={s.sectionLabel}>Broadcast history</p>
      {loading ? <p style={s.muted}>Loading…</p> : broadcasts.length === 0 ? (
        <p style={s.muted}>No broadcasts yet. Create your first campaign above.</p>
      ) : (
        <div style={s.histList}>
          {broadcasts.map(b => (
            <div key={b.id} style={s.histRow}>
              <div style={{ flex: 1 }}>
                <span style={s.histName}>{b.name || b.template_name}</span>
                <span style={s.histMeta}>
                  {b.audience_filter} · {b.status} · {new Date(b.created_at).toLocaleDateString('en-ZA')}
                </span>
              </div>
              <div style={s.histStats}>
                {b.sent_count > 0 && <span style={s.statChip}>✉ {b.sent_count}</span>}
                {b.delivered_count > 0 && <span style={s.statChip}>✓ {b.delivered_count}</span>}
                {b.read_count > 0 && <span style={{ ...s.statChip, color: 'var(--accent, #2C5545)' }}>👁 {b.read_count}</span>}
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
  sectionLabel: { fontFamily: 'system-ui', fontSize: 12, fontWeight: 600, color: '#1E1E1E', margin: '0 0 8px' },
  tplList:      { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 },
  tplBtn:       { background: '#F7F4EE', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 2 },
  tplBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, #2C5545)' },
  tplLabel:     { fontFamily: 'system-ui', fontSize: 13, fontWeight: 600, color: '#1E1E1E' },
  tplHint:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  audRow:       { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 },
  audBtn:       { background: '#F7F4EE', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', gap: 10, alignItems: 'center' },
  audBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, #2C5545)' },
  audLabel:     { fontFamily: 'system-ui', fontSize: 13, fontWeight: 600, color: '#1E1E1E', minWidth: 140 },
  audHint:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  preview:      { background: '#F7F4EE', borderRadius: 6, padding: '10px 14px', marginBottom: 14 },
  previewLabel: { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', margin: '0 0 4px' },
  previewText:  { fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E', margin: 0 },
  error:        { color: '#ef4444', fontSize: 13, fontFamily: 'system-ui', margin: '0 0 10px' },
  success:      { color: '#16a34a', fontSize: 13, fontFamily: 'system-ui', margin: '0 0 10px', fontWeight: 600 },
  sendBtn:      { width: '100%', padding: '12px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  btnDisabled:  { width: '100%', padding: '12px', background: '#DDD8CE', color: '#8A8680', border: 'none', borderRadius: 8, fontSize: 14, cursor: 'not-allowed', fontFamily: 'system-ui' },
  confirmBox:   { background: 'rgba(37,211,102,0.06)', border: '1px solid rgba(37,211,102,0.3)', borderRadius: 8, padding: 14 },
  confirmText:  { fontFamily: 'system-ui', fontSize: 13, color: '#1E1E1E', margin: '0 0 10px', lineHeight: 1.5 },
  sampleNames:  { color: '#8A8680' },
  confirmRow:   { display: 'flex', gap: 8 },
  cancelBtn:    { flex: 1, padding: '11px', background: 'transparent', color: '#8A8680', border: '1px solid #DDD8CE', borderRadius: 8, fontSize: 14, cursor: 'pointer', fontFamily: 'system-ui' },
  sendLiveBtn:  { flex: 2, padding: '11px', background: '#25D366', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  muted:        { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '16px 0' },
  histList:     { display: 'flex', flexDirection: 'column', gap: 6 },
  histRow:      { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10 },
  histName:     { display: 'block', fontFamily: 'system-ui', fontSize: 13, fontWeight: 600, color: '#1E1E1E' },
  histMeta:     { display: 'block', fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  histStats:    { display: 'flex', gap: 6 },
  statChip:     { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', background: '#F7F4EE', padding: '2px 8px', borderRadius: 10 },
}
