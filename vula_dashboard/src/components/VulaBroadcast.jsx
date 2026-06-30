/**
 * VulaBroadcast.jsx — WhatsApp broadcast / campaign builder.
 *
 * Send promotional messages to customers via approved Meta templates.
 * Audience filters: all / ordered last 30 days / high value (>R500).
 * History shows sent/delivered/read counts.
 */

import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
const API_KEY  = import.meta.env.VITE_API_KEY  || ''
const H = { 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) }

const TEMPLATES = [
  { id: 'weekly_fish',   label: '🐟 Weekly fish specials', hint: 'This week\'s fresh catch + prices' },
  { id: 'new_stock',     label: '📦 New stock arrived',    hint: 'Fresh delivery announcement' },
  { id: 'reorder',       label: '🔔 Reorder reminder',     hint: 'Customers who haven\'t ordered in 2+ weeks' },
  { id: 'promo',         label: '🎉 Seasonal promotion',   hint: 'Special discount or limited-time offer' },
  { id: 'delivery',      label: '🚚 Delivery reminder',    hint: 'Remind customers about delivery days' },
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
  const [audiences, setAudiences] = useState(['all'])
  const toggleAud = (id) => setAudiences(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  const [details, setDetails] = useState('')    // raw details Stacy types for the AI
  const [bodyText, setBodyText] = useState('')   // the actual message that gets sent
  const [drafting, setDrafting] = useState(false)
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(null)       // live-send result {sent, failed, recipient_count}
  const [preview, setPreview] = useState(null)  // dry-run result {recipient_count, sample}
  const [error, setError] = useState(null)
  const [scheduleAt, setScheduleAt] = useState('')   // datetime-local for a scheduled send
  const [recurrence, setRecurrence] = useState('once')
  const [campaigns, setCampaigns] = useState([])
  const [segments, setSegments] = useState([])
  const [showSeg, setShowSeg] = useState(false)
  const [segForm, setSegForm] = useState({ name: '', not_ordered_within_days: '', min_spend: '', channel: '' })

  // Ask the AI assistant to write the broadcast from rough details
  async function writeWithAI() {
    if (!details.trim()) { setError('Type a few details first (e.g. the catch + prices).'); return }
    setDrafting(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/draft`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ details, message_type: template }),
      })
      const d = await r.json()
      if (r.ok && d.message) setBodyText(d.message)
      else setError(d.detail || 'Could not write the message')
    } catch (err) {
      setError(err.message)
    } finally {
      setDrafting(false)
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts`)
      const d = await r.json()
      setBroadcasts(d.broadcasts || [])
    } catch {}
    setLoading(false)
  }, [tenantId])

  const loadCampaigns = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/campaigns`)
      const d = await r.json()
      setCampaigns((d.campaigns || []).filter(c => c.active))
    } catch {}
  }, [tenantId])

  const loadSegments = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/segments`)
      const d = await r.json()
      setSegments(d.segments || [])
    } catch {}
  }, [tenantId])

  useEffect(() => { load(); loadCampaigns(); loadSegments() }, [load, loadCampaigns, loadSegments])

  const segHint = (cr = {}) => [
    cr.not_ordered_within_days != null && `no order in ${cr.not_ordered_within_days}d`,
    cr.ordered_within_days != null && `ordered ≤ ${cr.ordered_within_days}d`,
    cr.min_spend != null && `spend ≥ R${cr.min_spend}`,
    cr.min_orders != null && `≥ ${cr.min_orders} orders`,
    cr.channel && cr.channel,
  ].filter(Boolean).join(' · ')

  async function createSegment() {
    const c = {}
    if (segForm.not_ordered_within_days) c.not_ordered_within_days = Number(segForm.not_ordered_within_days)
    if (segForm.min_spend) c.min_spend = Number(segForm.min_spend)
    if (segForm.channel) c.channel = segForm.channel
    if (!segForm.name.trim() || Object.keys(c).length === 0) { setError('Segment needs a name + at least one rule.'); return }
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/segments`, {
      method: 'POST', headers: H, body: JSON.stringify({ name: segForm.name, criteria: c }),
    })
    setSegForm({ name: '', not_ordered_within_days: '', min_spend: '', channel: '' }); setShowSeg(false); loadSegments()
  }
  async function deleteSegment(id) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/segments/${id}`, { method: 'DELETE' })
    setAudiences(prev => prev.filter(x => x !== `seg:${id}`))
    loadSegments()
  }

  // Schedule a broadcast for later (or recurring) instead of sending now.
  async function scheduleCampaign() {
    if (!bodyText.trim()) { setError('Write the message first.'); return }
    if (!scheduleAt) { setError('Pick a date & time.'); return }
    setSending(true); setError(null)
    try {
      const tpl = TEMPLATES.find(t => t.id === template)
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/campaigns`, {
        method: 'POST', headers: H,
        body: JSON.stringify({
          name: tpl?.label, body: bodyText, audience_filter: (audiences.join(',') || 'all'),
          recurrence, run_at: new Date(scheduleAt).toISOString(),
        }),
      })
      const d = await r.json()
      if (r.ok && d.id) { setScheduleAt(''); setRecurrence('once'); loadCampaigns() }
      else setError(d.detail || 'Could not schedule')
    } catch (err) { setError(err.message) } finally { setSending(false) }
  }

  async function deleteCampaign(id) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/campaigns/${id}`, { method: 'DELETE' })
    loadCampaigns()
  }

  // Step 1 — preview (dry-run): who would this reach? No messages sent.
  async function previewBroadcast() {
    if (!bodyText.trim()) { setError('Write the message first (type it or use ✨ Write with AI).'); return }
    setSending(true); setError(null); setSent(false); setPreview(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/send`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ body: bodyText, audience_filter: (audiences.join(',') || 'all'), dry_run: true }),
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
        method: 'POST', headers: H,
        body: JSON.stringify({ body: bodyText, audience_filter: (audiences.join(',') || 'all'), name: tpl?.label, dry_run: false }),
      })
      const d = await r.json()
      if (r.ok) { setSent(d); setPreview(null); setDetails(''); load() }
      else setError(d.detail || 'Send failed')
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  const selectedTpl = TEMPLATES.find(t => t.id === template)
  const audLabel = (id) => AUDIENCES.find(a => a.id === id)?.label
    || ('🎯 ' + (segments.find(sg => `seg:${sg.id}` === id)?.name || 'segment'))
  const selectedAudLabel = audiences.length ? audiences.map(audLabel).join(' + ') : 'No audience selected'

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

        {/* AI writer — type rough details, let Vula write the message */}
        <p style={s.sectionLabel}>Your details (let AI write it for you)</p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          <input
            value={details}
            onChange={e => setDetails(e.target.value)}
            placeholder="e.g. yellowtail R180, snoek R95, kob R220, free delivery over R500"
            style={{ ...s.input, flex: 1 }}
            onKeyDown={e => { if (e.key === 'Enter') writeWithAI() }}
          />
          <button onClick={writeWithAI} disabled={drafting} style={drafting ? s.btnDisabled : s.aiBtn}>
            {drafting ? 'Writing…' : '✨ Write with AI'}
          </button>
        </div>

        {/* The actual message — editable, whether typed or AI-written */}
        <p style={s.sectionLabel}>Message to send (edit anything)</p>
        <textarea
          value={bodyText}
          onChange={e => setBodyText(e.target.value)}
          placeholder="Type your message here, or use ✨ Write with AI above. This is exactly what customers receive."
          style={s.textarea}
          rows={4}
        />
        <p style={s.charCount}>{bodyText.length} characters</p>

        <p style={s.sectionLabel}>Audience <span style={{ fontWeight: 400, color: '#8A8680' }}>— pick one or more; overlaps are de-duplicated</span></p>
        <div style={s.audRow}>
          {AUDIENCES.map(a => (
            <button
              key={a.id}
              onClick={() => toggleAud(a.id)}
              style={{ ...s.audBtn, ...(audiences.includes(a.id) ? s.audBtnActive : {}) }}
            >
              <span style={s.audLabel}>{audiences.includes(a.id) ? '☑ ' : '☐ '}{a.label}</span>
              <span style={s.audHint}>{a.hint}</span>
            </button>
          ))}
          {segments.map(seg => (
            <button key={seg.id} onClick={() => toggleAud(`seg:${seg.id}`)}
              style={{ ...s.audBtn, ...(audiences.includes(`seg:${seg.id}`) ? s.audBtnActive : {}) }}>
              <span style={s.audLabel}>{audiences.includes(`seg:${seg.id}`) ? '☑ ' : '☐ '}🎯 {seg.name}
                <span onClick={(e) => { e.stopPropagation(); deleteSegment(seg.id) }}
                  style={{ marginLeft: 6, color: '#C0392B', cursor: 'pointer' }}>×</span>
              </span>
              <span style={s.audHint}>{segHint(seg.criteria)}</span>
            </button>
          ))}
          <button onClick={() => setShowSeg(v => !v)} style={{ ...s.audBtn, borderStyle: 'dashed' }}>
            <span style={s.audLabel}>＋ New segment</span>
            <span style={s.audHint}>custom rules</span>
          </button>
        </div>
        {showSeg && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', margin: '0 0 12px' }}>
            <input placeholder="Segment name" value={segForm.name} onChange={e => setSegForm({ ...segForm, name: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid #DDD8CE', borderRadius: 7, fontSize: 12 }} />
            <input type="number" placeholder="no order in … days" value={segForm.not_ordered_within_days}
              onChange={e => setSegForm({ ...segForm, not_ordered_within_days: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid #DDD8CE', borderRadius: 7, fontSize: 12, width: 150 }} />
            <input type="number" placeholder="min spend (R)" value={segForm.min_spend}
              onChange={e => setSegForm({ ...segForm, min_spend: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid #DDD8CE', borderRadius: 7, fontSize: 12, width: 120 }} />
            <select value={segForm.channel} onChange={e => setSegForm({ ...segForm, channel: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid #DDD8CE', borderRadius: 7, fontSize: 12 }}>
              <option value="">any channel</option><option value="whatsapp">WhatsApp</option><option value="web">Web</option>
            </select>
            <button onClick={createSegment} style={s.sendBtn}>Save segment</button>
          </div>
        )}

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
            <strong>{selectedTpl?.label}</strong> → <strong>{selectedAudLabel}</strong>
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
            {preview.suppressed_count > 0 && (
              <p style={s.sampleNames}>
                🚫 {preview.suppressed_count} opted-out contact{preview.suppressed_count !== 1 ? 's' : ''} excluded (POPIA)
              </p>
            )}
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

      {/* Schedule for later / recurring */}
      <p style={s.sectionLabel}>📅 Schedule for later</p>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <input type="datetime-local" value={scheduleAt} onChange={e => setScheduleAt(e.target.value)}
          style={{ padding: '8px 10px', border: '1px solid #DDD8CE', borderRadius: 8, fontSize: 13 }} />
        <select value={recurrence} onChange={e => setRecurrence(e.target.value)}
          style={{ padding: '8px 10px', border: '1px solid #DDD8CE', borderRadius: 8, fontSize: 13 }}>
          <option value="once">One-off</option>
          <option value="daily">Every day</option>
          <option value="weekly">Every week</option>
          <option value="monthly">Every month</option>
        </select>
        <button onClick={scheduleCampaign} disabled={sending} style={sending ? s.btnDisabled : s.sendBtn}>
          {sending ? '…' : 'Schedule'}
        </button>
        <span style={s.muted}>Uses the message + audience above. Recurring repeats from this time.</span>
      </div>
      {campaigns.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          {campaigns.map(c => (
            <div key={c.id} style={s.histRow}>
              <div style={{ flex: 1 }}>
                <span style={s.histName}>{c.name}</span>
                <span style={s.histMeta}>
                  {c.audience_filter} · {c.recurrence} · next {new Date(c.next_run_at).toLocaleString('en-ZA')}
                </span>
              </div>
              <button onClick={() => deleteCampaign(c.id)} style={s.cancelBtn}>Cancel</button>
            </div>
          ))}
        </div>
      )}

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
                {b.read_count > 0 && <span style={{ ...s.statChip, color: 'var(--accent, var(--accent))' }}>👁 {b.read_count}</span>}
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
  charCount:    { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', margin: '4px 0 0', textAlign: 'right' },
  tplList:      { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 },
  tplBtn:       { background: '#F7F4EE', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 2 },
  tplBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, var(--accent))' },
  tplLabel:     { fontFamily: 'system-ui', fontSize: 13, fontWeight: 600, color: '#1E1E1E' },
  tplHint:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
  audRow:       { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 },
  audBtn:       { background: '#F7F4EE', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', gap: 10, alignItems: 'center' },
  audBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, var(--accent))' },
  audLabel:     { fontFamily: 'system-ui', fontSize: 13, fontWeight: 600, color: '#1E1E1E', minWidth: 140 },
  audHint:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680' },
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
