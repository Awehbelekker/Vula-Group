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
  { id: 'active_30d',   label: 'Active (last 30 days)',    hint: 'Customers who ordered recently' },
  { id: 'high_value',   label: 'High-value customers',    hint: 'Customers with total orders > R500' },
]

export default function VulaBroadcast({ tenantId, draftBody, onConsumeDraft }) {
  const [broadcasts, setBroadcasts] = useState([])
  const [funnelId, setFunnelId] = useState(null)   // expanded broadcast → funnel drawer (P3)
  const [loading, setLoading] = useState(true)
  const [template, setTemplate] = useState(TEMPLATES[0].id)
  const [audiences, setAudiences] = useState(['all'])
  const toggleAud = (id) => setAudiences(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  const [details, setDetails] = useState('')    // raw details Stacy types for the AI
  const [bodyText, setBodyText] = useState('')   // the actual message that gets sent
  const [targetUrl, setTargetUrl] = useState('')  // optional link — click-tracked per recipient
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
  const [counts, setCounts] = useState({ counts: {}, segments: {} })
  const [testPhone, setTestPhone] = useState('')
  const [testMsg, setTestMsg] = useState('')
  const [realTemplates, setRealTemplates] = useState([])   // approved templates from the 📨 Templates tab
  const [useApproved, setUseApproved] = useState(false)     // free text (default) vs. an approved template
  const [approvedName, setApprovedName] = useState('')
  const [headerImageUrl, setHeaderImageUrl] = useState('')  // real image for a template with an IMAGE header

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

  const loadCounts = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/audience-counts`)
      setCounts(await r.json())
    } catch {}
  }, [tenantId])

  const loadRealTemplates = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/wa-templates`)
      const d = await r.json()
      setRealTemplates((d.templates || []).filter(t => t.status === 'APPROVED'))
    } catch {}
  }, [tenantId])

  useEffect(() => { load(); loadCampaigns(); loadSegments(); loadCounts(); loadRealTemplates() }, [load, loadCampaigns, loadSegments, loadCounts, loadRealTemplates])

  // Marketing tab handed off a chosen copy variant — land it in the free-text body (P2.1).
  useEffect(() => {
    if (!draftBody) return
    setUseApproved(false)
    setBodyText(draftBody)
    onConsumeDraft && onConsumeDraft()
  }, [draftBody])  // eslint-disable-line

  const approvedTpl = realTemplates.find(t => t.name === approvedName)

  // Send the message to your own number only — a live preview before broadcasting.
  async function sendTest() {
    if (useApproved && !approvedName) { setError('Pick an approved template first.'); return }
    if (useApproved && approvedTpl?.header_type === 'IMAGE' && !headerImageUrl.trim()) { setError('This template has an image header — add an image URL.'); return }
    if (!useApproved && !bodyText.trim()) { setError('Write the message first.'); return }
    if (!testPhone.trim()) { setError('Enter a phone number to test to.'); return }
    setTestMsg(''); setError(null)
    try {
      const payload = useApproved
        ? { template_name: approvedName, header_image_url: headerImageUrl || undefined, target_url: targetUrl || undefined, test_phone: testPhone, dry_run: false }
        : { body: bodyText, target_url: targetUrl || undefined, test_phone: testPhone, dry_run: false }
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/send`, {
        method: 'POST', headers: H, body: JSON.stringify(payload),
      })
      const d = await r.json()
      setTestMsg(r.ok && d.sent ? `✓ Test sent to ${testPhone}` : (d.detail || `Could not send test (${d.failed || 0} failed)`))
    } catch (err) { setError(err.message) }
  }

  const audCount = (id) => id.startsWith('seg:') ? counts.segments?.[id.slice(4)] : counts.counts?.[id]

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
    if (useApproved && !approvedName) { setError('Pick an approved template first.'); return }
    if (!useApproved && !bodyText.trim()) { setError('Write the message first (type it or use ✨ Write with AI).'); return }
    setSending(true); setError(null); setSent(false); setPreview(null)
    try {
      const payload = useApproved
        ? { template_name: approvedName, audience_filter: (audiences.join(',') || 'all'), dry_run: true }
        : { body: bodyText, audience_filter: (audiences.join(',') || 'all'), dry_run: true }
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/send`, {
        method: 'POST', headers: H, body: JSON.stringify(payload),
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
    if (useApproved && approvedTpl?.header_type === 'IMAGE' && !headerImageUrl.trim()) { setError('This template has an image header — add an image URL.'); return }
    setSending(true); setError(null); setSent(false)
    try {
      const tpl = TEMPLATES.find(t => t.id === template)
      const payload = useApproved
        ? { template_name: approvedName, audience_filter: (audiences.join(',') || 'all'), name: approvedName, header_image_url: headerImageUrl || undefined, target_url: targetUrl || undefined, dry_run: false }
        : { body: bodyText, audience_filter: (audiences.join(',') || 'all'), name: tpl?.label, target_url: targetUrl || undefined, dry_run: false }
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/send`, {
        method: 'POST', headers: H, body: JSON.stringify(payload),
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
        {/* Free text (within 24h of a customer's last message) vs. an approved template
            (works cold, and can carry a header image / buttons — see 📨 Templates tab) */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <button onClick={() => setUseApproved(false)} style={{ ...s.tplBtn, flex: 1, ...(!useApproved ? s.tplBtnActive : {}) }}>
            <span style={s.tplLabel}>💬 Free text</span>
            <span style={s.tplHint}>Only reaches customers active in the last 24h</span>
          </button>
          <button onClick={() => setUseApproved(true)} style={{ ...s.tplBtn, flex: 1, ...(useApproved ? s.tplBtnActive : {}) }}>
            <span style={s.tplLabel}>📨 Approved template</span>
            <span style={s.tplHint}>{realTemplates.length ? `${realTemplates.length} approved — works cold, supports images/buttons` : 'None approved yet — see 📨 Templates tab'}</span>
          </button>
        </div>

        {useApproved ? (
          <>
            <p style={s.sectionLabel}>Which template</p>
            <select value={approvedName} onChange={e => setApprovedName(e.target.value)} style={{ ...s.input, width: '100%', boxSizing: 'border-box' }}>
              <option value="">Choose an approved template…</option>
              {realTemplates.map(t => <option key={t.name} value={t.name}>{t.name} ({t.category})</option>)}
            </select>
            {approvedTpl && (
              <div style={{ ...s.textarea, background: '#F8F7F2', color: '#5B5750', display: 'flex', alignItems: 'center' }}>
                {approvedTpl.body_text}
              </div>
            )}
            {approvedTpl?.header_type === 'IMAGE' && (
              <input value={headerImageUrl} onChange={e => setHeaderImageUrl(e.target.value)}
                placeholder="Image URL for this send (required — the template has an image header)"
                style={{ ...s.input, width: '100%', boxSizing: 'border-box', marginTop: 6 }} />
            )}
          </>
        ) : (
          <>
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
          </>
        )}

        {/* Optional trackable link — appended to free text, or fills a template's URL button */}
        <input value={targetUrl} onChange={e => setTargetUrl(e.target.value)}
          placeholder="Optional: link to track clicks on (e.g. https://offthehook.co.za/menu)"
          style={{ ...s.input, width: '100%', boxSizing: 'border-box', marginTop: 6 }} />

        {/* Send a test to your own number before broadcasting */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
          <input value={testPhone} onChange={e => setTestPhone(e.target.value)} placeholder="Test to: 2782…"
            style={{ ...s.input, width: 170 }} />
          <button onClick={sendTest} style={{ ...s.aiBtn, background: '#5B6B7A' }}>📲 Send test</button>
          {testMsg && <span style={{ fontSize: 12, fontFamily: 'system-ui', color: testMsg.startsWith('✓') ? '#16a34a' : '#ef4444' }}>{testMsg}</span>}
        </div>

        <p style={s.sectionLabel}>Audience <span style={{ fontWeight: 400, color: '#8A8680' }}>— pick one or more; overlaps are de-duplicated</span></p>
        <div style={s.audRow}>
          {AUDIENCES.map(a => (
            <button
              key={a.id}
              onClick={() => toggleAud(a.id)}
              style={{ ...s.audBtn, ...(audiences.includes(a.id) ? s.audBtnActive : {}) }}
            >
              <span style={s.audLabel}>{audiences.includes(a.id) ? '☑ ' : '☐ '}{a.label}
                {audCount(a.id) != null && <span style={s.reachBadge}>{audCount(a.id)} reachable</span>}</span>
              <span style={s.audHint}>{a.hint}</span>
            </button>
          ))}
          {segments.map(seg => (
            <button key={seg.id} onClick={() => toggleAud(`seg:${seg.id}`)}
              style={{ ...s.audBtn, ...(audiences.includes(`seg:${seg.id}`) ? s.audBtnActive : {}) }}>
              <span style={s.audLabel}>{audiences.includes(`seg:${seg.id}`) ? '☑ ' : '☐ '}🎯 {seg.name}
                {audCount(`seg:${seg.id}`) != null && <span style={s.reachBadge}>{audCount(`seg:${seg.id}`)} reachable</span>}
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
      <p style={{ ...s.audHint, margin: '-4px 0 8px' }}>💡 Food promos land best on weekday late afternoons (≈ 4–6pm), before dinner planning.</p>
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
            <div key={b.id}>
              <div style={{ ...s.histRow, cursor: 'pointer' }}
                   onClick={() => setFunnelId(funnelId === b.id ? null : b.id)}>
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
                  {b.clicked_count > 0 && <span style={{ ...s.statChip, color: 'var(--accent, var(--accent))' }}>🔗 {b.clicked_count}</span>}
                  <span style={{ ...s.statChip, color: '#8A8680' }}>{funnelId === b.id ? '▴' : '▾'}</span>
                </div>
              </div>
              {funnelId === b.id && <BroadcastFunnel tenantId={tenantId} broadcastId={b.id} />}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function BroadcastFunnel({ tenantId, broadcastId }) {
  const [data, setData] = useState(null)
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/${broadcastId}/recipients`)
      .then(r => r.json()).then(setData).catch(() => setData({ funnel: {}, recipients: [] }))
  }, [tenantId, broadcastId])
  if (!data) return <p style={{ fontSize: 12, color: '#8A8680', padding: '6px 12px' }}>Loading funnel…</p>
  const f = data.funnel || {}
  const stages = [
    { label: 'Sent', n: f.sent || 0, color: '#8A8680' },
    { label: 'Delivered', n: f.delivered || 0, color: 'var(--accent, #2C5545)' },
    { label: 'Read', n: f.read || 0, color: 'var(--accent, #2C5545)' },
    { label: 'Clicked', n: f.clicked || 0, color: 'var(--accent, #2C5545)' },
    ...(f.failed ? [{ label: 'Failed', n: f.failed, color: '#A23B2D' }] : []),
  ]
  const max = Math.max(1, f.sent || 1)
  const clickers = (data.recipients || []).filter(r => r.clicked_at)
  const failures = (data.recipients || []).filter(r => r.status === 'failed')
  return (
    <div style={{ background: '#F0EDE5', borderRadius: 8, padding: '10px 14px', margin: '2px 0 8px' }}>
      {stages.map(st => (
        <div key={st.label} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', fontSize: 12 }}>
          <span style={{ width: 66, color: '#8A8680' }}>{st.label}</span>
          <div style={{ flex: 1, height: 8, background: '#fff', borderRadius: 99, overflow: 'hidden' }}>
            <div style={{ width: `${Math.round(100 * st.n / max)}%`, height: '100%', background: st.color, borderRadius: 99 }} />
          </div>
          <span style={{ width: 34, textAlign: 'right', fontFamily: 'monospace', color: '#1E1E1E' }}>{st.n}</span>
        </div>
      ))}
      {clickers.length > 0 && (
        <p style={{ fontSize: 11.5, color: '#8A8680', margin: '8px 0 0' }}>
          🔗 Clicked: {clickers.slice(0, 8).map(r => r.phone).join(', ')}{clickers.length > 8 ? ` +${clickers.length - 8} more` : ''}
        </p>
      )}
      {failures.length > 0 && (
        <p style={{ fontSize: 11.5, color: '#A23B2D', margin: '6px 0 0' }}>
          ⚠ Failed: {failures.slice(0, 5).map(r => `${r.phone}${r.error ? ` (${String(r.error).slice(0, 30)})` : ''}`).join(', ')}{failures.length > 5 ? ` +${failures.length - 5}` : ''}
        </p>
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
  reachBadge:   { marginLeft: 8, fontFamily: 'system-ui', fontSize: 10, fontWeight: 600, color: 'var(--accent)', background: 'rgba(44,85,69,0.10)', padding: '1px 7px', borderRadius: 10 },
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
