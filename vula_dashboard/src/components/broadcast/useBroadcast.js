/**
 * useBroadcast.js — Marketing › Broadcast, shared by VulaBroadcast (current look) and
 * broadcast/BroadcastV2 (the redesign, 2026-10-20). Moved verbatim: the picture promotion (a
 * product photo + offer + Shop now, through the approved promotion template), the compose →
 * preview audience (dry run, nothing sent) → confirmed live send flow, test-to-my-number,
 * segments, scheduled / recurring campaigns, analytics and each broadcast's funnel. Opted-out
 * contacts are excluded server-side (POPIA) and the preview says how many.
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'

const H = { 'Content-Type': 'application/json' }  // JWT attached by lib/authFetch

export const TEMPLATES = [
  { id: 'weekly_fish',   label: '🐟 Weekly fish specials', hint: 'This week\'s fresh catch + prices' },
  { id: 'new_stock',     label: '📦 New stock arrived',    hint: 'Fresh delivery announcement' },
  { id: 'reorder',       label: '🔔 Reorder reminder',     hint: 'Customers who haven\'t ordered in 2+ weeks' },
  { id: 'promo',         label: '🎉 Seasonal promotion',   hint: 'Special discount or limited-time offer' },
  { id: 'delivery',      label: '🚚 Delivery reminder',    hint: 'Remind customers about delivery days' },
]

export const AUDIENCES = [
  { id: 'all',          label: 'All customers',            hint: 'Everyone who has ever ordered' },
  { id: 'active_30d',   label: 'Active (last 30 days)',    hint: 'Customers who ordered recently' },
  { id: 'high_value',   label: 'High-value customers',    hint: 'Customers with total orders > R500' },
]

export const SEG_LANGUAGES = {
  en: 'English', af: 'Afrikaans', zu: 'isiZulu', xh: 'isiXhosa', st: 'Sesotho',
  nso: 'Sepedi', tn: 'Setswana', ts: 'Xitsonga', ve: 'Tshivenda', ss: 'siSwati', nr: 'isiNdebele',
}

export function usePicturePromo(tenantId) {
  const [products, setProducts] = useState([])
  const [productId, setProductId] = useState('')
  const [message, setMessage] = useState('')
  const [audience, setAudience] = useState('all')
  const [testPhone, setTestPhone] = useState('')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products`).then(r => r.json())
      .then(d => setProducts((d.products || d || []).filter(p => p.image_url && p.status !== 'archived')))
      .catch(() => {})
  }, [tenantId])

  const chosen = products.find(p => String(p.id) === String(productId))

  async function call(extra) {
    setBusy(true); setNote(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/promo`, {
        method: 'POST', headers: H,
        body: JSON.stringify({ message, product_id: productId || undefined, audience_filter: audience, ...extra }),
      })
      const d = await r.json()
      if (!r.ok) { setNote({ ok: false, text: d.detail || 'Could not do that.' }); return null }
      return d
    } catch (e) { setNote({ ok: false, text: e.message }); return null } finally { setBusy(false) }
  }

  async function doPreview() {
    const d = await call({ dry_run: true })
    if (d) setPreview(d)
  }
  async function doTest() {
    if (!testPhone.trim()) { setNote({ ok: false, text: 'Enter your WhatsApp number to test to.' }); return }
    const d = await call({ dry_run: false, test_phone: testPhone })
    if (d) setNote({ ok: !!d.sent, text: d.sent ? `✓ Test sent to ${testPhone}` : (d.errors?.[0] || 'The test did not go through.') })
  }
  // confirm: window.confirm in the current look, the redesign's confirmDialog in BroadcastV2
  async function doSend(confirm = (m) => window.confirm(m)) {
    if (!preview) return
    if (!(await confirm(`Send this promotion to ${preview.recipient_count} customers?`))) return
    const d = await call({ dry_run: false })
    if (d) setNote({ ok: true, text: d.queued ? `📣 Sending to ${d.recipient_count} customers — progress shows in the history below.` : `📣 Sent to ${d.sent} of ${d.recipient_count} customers.` })
  }

  const approved = preview?.template_status === 'APPROVED'

  return { products, productId, setProductId, message, setMessage, audience, setAudience, testPhone, setTestPhone, preview, setPreview, busy, note, chosen, doPreview, doTest, doSend, approved }
}

export function useBroadcast(tenantId, draftBody, onConsumeDraft) {
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
  const [segForm, setSegForm] = useState({ name: '', not_ordered_within_days: '', min_spend: '', channel: '', tags: '', area: '', language: '' })
  const [counts, setCounts] = useState({ counts: {}, segments: {} })
  const [testPhone, setTestPhone] = useState('')
  const [testMsg, setTestMsg] = useState('')
  const [realTemplates, setRealTemplates] = useState([])   // approved templates from the 📨 Templates tab
  const [analytics, setAnalytics] = useState(null)   // aggregated marketing analytics rollup
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

  const loadAnalytics = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/analytics`)
      setAnalytics(await r.json())
    } catch {}
  }, [tenantId])

  useEffect(() => { load(); loadCampaigns(); loadSegments(); loadCounts(); loadRealTemplates(); loadAnalytics() }, [load, loadCampaigns, loadSegments, loadCounts, loadRealTemplates, loadAnalytics])

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
    if (!useApproved) { setError('WhatsApp only allows broadcasts from an approved template — pick one, or turn this text into a new template in the 📨 Templates tab.'); return }
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
    cr.tags?.length && `🏷 ${cr.tags.join(', ')}`,
    cr.area && `📍 ${cr.area}`,
    cr.language && `🗣️ ${SEG_LANGUAGES[cr.language] || cr.language}`,
  ].filter(Boolean).join(' · ')

  async function createSegment() {
    const c = {}
    if (segForm.not_ordered_within_days) c.not_ordered_within_days = Number(segForm.not_ordered_within_days)
    if (segForm.min_spend) c.min_spend = Number(segForm.min_spend)
    if (segForm.channel) c.channel = segForm.channel
    if (segForm.tags.trim()) c.tags = segForm.tags.split(',').map(t => t.trim()).filter(Boolean)
    if (segForm.area.trim()) c.area = segForm.area.trim()
    if (segForm.language) c.language = segForm.language
    if (!segForm.name.trim() || Object.keys(c).length === 0) { setError('Segment needs a name + at least one rule.'); return }
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/segments`, {
      method: 'POST', headers: H, body: JSON.stringify({ name: segForm.name, criteria: c }),
    })
    setSegForm({ name: '', not_ordered_within_days: '', min_spend: '', channel: '', tags: '', area: '', language: '' }); setShowSeg(false); loadSegments()
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
    if (!useApproved) { setError('WhatsApp only allows broadcasts from an approved template — pick one, or turn this text into a new template in the 📨 Templates tab.'); return }
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

  return {
    analytics, approvedName, approvedTpl, audCount, audLabel, audiences, bodyText, broadcasts, campaigns, counts, createSegment, deleteCampaign, deleteSegment, details, drafting, error, funnelId, headerImageUrl, loading, preview, previewBroadcast, realTemplates, recurrence, scheduleAt, scheduleCampaign, segForm, segHint, segments, selectedAudLabel, selectedTpl, sendBroadcast, sendTest, sending, sent, setApprovedName, setAudiences, setBodyText, setDetails, setError, setFunnelId, setHeaderImageUrl, setPreview, setRecurrence, setScheduleAt, setSegForm, setSending, setShowSeg, setTargetUrl, setTemplate, setTestMsg, setTestPhone, setUseApproved, showSeg, targetUrl, template, testMsg, testPhone, toggleAud, useApproved, writeWithAI,
  }
}

export function useBroadcastFunnel(tenantId, broadcastId) {
  const [data, setData] = useState(null)
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/broadcasts/${broadcastId}/recipients`)
      .then(r => r.json()).then(setData).catch(() => setData({ funnel: {}, recipients: [] }))
  }, [tenantId, broadcastId])
  return data
}
