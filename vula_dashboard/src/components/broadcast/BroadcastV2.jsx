/**
 * BroadcastV2.jsx — the redesigned Marketing › Broadcast (2026-10-20, UX redesign Phase F). Same
 * calls and the same guard rails as VulaBroadcast (broadcast/useBroadcast.js): nothing reaches a
 * customer without the owner first previewing the audience (a dry run that sends nothing) and
 * then tapping "Send live to N"; the picture promotion asks before it sends; opted-out contacts are
 * left out server-side (POPIA) and the preview says how many. Amounts go through formatMoney.
 */
import { BarChart3, CalendarClock, ChevronDown, Eye, FileCheck2, Image as ImageIcon, Link2, Megaphone, MessageSquare, MousePointerClick,
  Plus, Send, Smartphone, Sparkles, Trash2, Users, X } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { SA_TZ } from '../../lib/dates'
import { Button, Card, EmptyState, Field, IconButton, Input, PillTabs, Select, StatCard, StatusChip, Textarea, confirmDialog } from '../ui/index.jsx'
import { AUDIENCES, SEG_LANGUAGES, TEMPLATES, useBroadcast, useBroadcastFunnel, usePicturePromo } from './useBroadcast'
import './broadcast.css'

const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+\s*/, '')   // the shared labels and messages start with an emoji
const money = (c) => formatMoney(c || 0, { cents: 'always' })
const when = (iso, opts) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('en-ZA', { ...opts, timeZone: SA_TZ })
}
const KINDS = [
  { id: 'free', label: 'Free text', Icon: MessageSquare },
  { id: 'approved', label: 'Approved template', Icon: FileCheck2 },
]

export default function BroadcastV2({ tenantId, draftBody, onConsumeDraft }) {
  const b = useBroadcast(tenantId, draftBody, onConsumeDraft)
  return (
    <section className="vbc" aria-label="WhatsApp broadcasts">
      <p className="vbc-sub">Send promotional messages to your customers through approved WhatsApp templates. You always see who it reaches before anything is sent.</p>
      <PicturePromoV2 tenantId={tenantId} />
      <Compose b={b} />
      <Schedule b={b} />
      {b.analytics && b.analytics.totals?.broadcasts > 0 && <Analytics analytics={b.analytics} />}
      <History b={b} tenantId={tenantId} />
    </section>
  )
}

// ── Picture promotion ─────────────────────────────────────────────────────────

function PicturePromoV2({ tenantId }) {
  const p = usePicturePromo(tenantId)
  const img = p.preview?.image_url || p.chosen?.image_url || p.products[0]?.image_url
  const reach = p.preview?.recipient_count
  return (
    <Card className="vbc-card" role="group" aria-labelledby="vbc-promo">
      <h2 className="vbc-h" id="vbc-promo"><ImageIcon size={18} aria-hidden="true" />Picture promotion</h2>
      <p className="vbc-sub">A product photo, your offer and a <b>Shop now</b> button. It reaches customers even if they haven't messaged lately.</p>
      <div className="vbc-promo">
        <div className="vbc-form">
          <Field label="Photo">
            <Select value={p.productId} onChange={e => { p.setProductId(e.target.value); p.setPreview(null) }}>
              <option value="">First product in the shop</option>
              {p.products.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Select>
          </Field>
          <Field label="Your offer" hint={`${p.message.length}/500`}>
            <Textarea rows={3} maxLength={500} value={p.message} placeholder="e.g. 15% off all basins this weekend only!"
              onChange={e => { p.setMessage(e.target.value); p.setPreview(null) }} />
          </Field>
          <Field label="Who gets it">
            <Select value={p.audience} onChange={e => { p.setAudience(e.target.value); p.setPreview(null) }}>
              {AUDIENCES.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
            </Select>
          </Field>
          <div className="vbc-row">
            <Button variant="secondary" Icon={Eye} disabled={p.busy || !p.message.trim()} onClick={p.doPreview}>Preview</Button>
          </div>
          {p.preview && (
            <p className="vbc-body" role="status">
              Reaches <b>{reach}</b> customer{reach === 1 ? '' : 's'}
              {p.preview.suppressed_count ? ` (${p.preview.suppressed_count} opted out, left out)` : ''}.
              {p.preview.template_status !== 'APPROVED' && <> The promotion template is <b>{String(p.preview.template_status || '').toLowerCase()}</b>. Submit the standard templates under Settings › WhatsApp templates; Meta usually approves within a day.</>}
            </p>)}
          <form className="vbc-row vbc-bottom" onSubmit={e => { e.preventDefault(); p.doTest() }}>
            <Field label="Test to your number" style={{ flex: '1 1 160px', minWidth: 0 }}>
              <Input type="tel" value={p.testPhone} onChange={e => p.setTestPhone(e.target.value)} placeholder="e.g. 082 123 4567" />
            </Field>
            <Button type="submit" variant="secondary" Icon={Smartphone} disabled={p.busy || !p.message.trim() || !p.approved}>Test to me</Button>
          </form>
          <Button Icon={Megaphone} disabled={p.busy || !p.preview || !p.approved || !reach}
            onClick={() => p.doSend(m => confirmDialog(m, { confirmLabel: 'Send promotion' }))}>
            {reach ? `Send promotion to ${reach}` : 'Send promotion'}</Button>
          {p.note && <p className={p.note.ok ? 'vbc-ok' : 'vbc-error'} role={p.note.ok ? 'status' : 'alert'}>{plain(p.note.text)}</p>}
        </div>
        <figure className="vbc-phone" aria-label="How it looks in WhatsApp">
          <div className="vbc-bubble">
            {img && <img src={img} alt="" />}
            <p>Hi Thandi! {p.message.trim() || 'Your offer goes here.'} Tap Shop now to have a look. Reply STOP if you'd rather not get offers.</p>
            <span className="vbc-cta"><Link2 size={14} aria-hidden="true" />Shop now</span>
          </div>
          <figcaption className="vbc-muted">How it looks in WhatsApp</figcaption>
        </figure>
      </div>
    </Card>
  )
}

// ── Compose → preview audience → confirmed send ───────────────────────────────

function Compose({ b }) {
  const tpl = TEMPLATES.find(t => t.id === b.template)
  const audName = (id) => plain(AUDIENCES.find(a => a.id === id)?.label || b.segments.find(sg => `seg:${sg.id}` === id)?.name || 'segment')
  const what = b.useApproved ? (b.approvedName || 'an approved template') : plain(b.selectedTpl?.label)
  const who = b.audiences.length ? b.audiences.map(audName).join(' + ') : 'No audience selected'
  const n = b.preview?.recipient_count
  return (
    <Card className="vbc-card" role="group" aria-labelledby="vbc-compose">
      <h2 className="vbc-h" id="vbc-compose"><Megaphone size={18} aria-hidden="true" />New broadcast</h2>
      <PillTabs label="Message kind" tabs={KINDS} active={b.useApproved ? 'approved' : 'free'} onChange={id => b.setUseApproved(id === 'approved')} />
      <p className="vbc-muted">
        {b.useApproved
          ? (b.realTemplates.length ? `${b.realTemplates.length} approved. Works even if a customer hasn't messaged lately, and can carry an image or buttons.` : 'None approved yet: see the WhatsApp templates tab.')
          : 'Draft and preview here. WhatsApp only lets a broadcast go out from an approved template.'}
      </p>

      {b.useApproved ? (
        <>
          <Field label="Template">
            <Select value={b.approvedName} onChange={e => b.setApprovedName(e.target.value)}>
              <option value="">Choose an approved template…</option>
              {b.realTemplates.map(t => <option key={t.name} value={t.name}>{t.name} ({t.category})</option>)}
            </Select>
          </Field>
          {b.approvedTpl && <p className="vbc-quote">{b.approvedTpl.body_text}</p>}
          {b.approvedTpl?.header_type === 'IMAGE' && (
            <Field label="Image for this send" hint="Required: this template has an image header.">
              <Input type="url" value={b.headerImageUrl} onChange={e => b.setHeaderImageUrl(e.target.value)} placeholder="https://…" />
            </Field>)}
        </>
      ) : (
        <>
          <Field label="Kind of message" hint={tpl?.hint}>
            <Select value={b.template} onChange={e => b.setTemplate(e.target.value)}>
              {TEMPLATES.map(t => <option key={t.id} value={t.id}>{plain(t.label)}</option>)}
            </Select>
          </Field>
          <form className="vbc-row vbc-bottom" onSubmit={e => { e.preventDefault(); b.writeWithAI() }}>
            <Field label="Your details (Vula writes it for you)" style={{ flex: '1 1 240px', minWidth: 0 }}>
              <Input value={b.details} onChange={e => b.setDetails(e.target.value)} placeholder="e.g. yellowtail R180, snoek R95, free delivery over R500" />
            </Field>
            <Button type="submit" variant="secondary" Icon={Sparkles} disabled={b.drafting}>{b.drafting ? 'Writing…' : 'Write with AI'}</Button>
          </form>
          <Field label="Message to send" hint={`${b.bodyText.length} characters. This is exactly what customers receive.`}>
            <Textarea rows={4} value={b.bodyText} onChange={e => b.setBodyText(e.target.value)} placeholder="Type your message, or use Write with AI above." />
          </Field>
        </>
      )}

      <Field label="Link to track clicks on" hint="Optional. Added to free text, or fills a template's link button.">
        <Input type="url" value={b.targetUrl} onChange={e => b.setTargetUrl(e.target.value)} placeholder="e.g. https://offthehook.co.za/menu" />
      </Field>

      <form className="vbc-row vbc-bottom" onSubmit={e => { e.preventDefault(); b.sendTest() }}>
        <Field label="Send a test to" style={{ flex: '1 1 160px', minWidth: 0 }}>
          <Input type="tel" value={b.testPhone} onChange={e => b.setTestPhone(e.target.value)} placeholder="2782…" />
        </Field>
        <Button type="submit" variant="secondary" Icon={Smartphone}>Send test</Button>
      </form>
      {b.testMsg && <p className={b.testMsg.startsWith('✓') ? 'vbc-ok' : 'vbc-error'} role="status">{plain(b.testMsg)}</p>}

      <Audience b={b} />

      {b.error && <p className="vbc-error" role="alert">{plain(b.error)}</p>}
      {b.sent && (
        <p className="vbc-ok" role="status">
          {b.sent.queued
            ? `Sending to ${b.sent.recipient_count} recipients in the background (about ${Math.ceil(b.sent.recipient_count / 600)} min). Progress shows in the history below.`
            : `Sent to ${b.sent.sent} recipient${b.sent.sent !== 1 ? 's' : ''}${b.sent.failed ? ` · ${b.sent.failed} failed` : ''}.`}
        </p>)}

      <div className="vbc-summary"><span className="vbc-muted">About to broadcast</span><b>{what} → {who}</b></div>

      {!b.preview ? (
        <div className="vbc-actions vbc-actions--sticky">
          <Button Icon={Eye} disabled={b.sending} onClick={b.previewBroadcast}>{b.sending ? 'Checking…' : 'Preview audience'}</Button>
        </div>
      ) : (
        <div className="vbc-confirm" role="group" aria-label="Confirm the broadcast">
          <p className="vbc-body">
            This will send <b>{what}</b> to <b>{n}</b> customer{n !== 1 ? 's' : ''}
            {b.preview.sample?.length > 0 && <span className="vbc-muted"> (e.g. {b.preview.sample.slice(0, 3).map(r => r.name).join(', ')}{n > 3 ? '…' : ''})</span>}
          </p>
          {b.preview.suppressed_count > 0 && (
            <p className="vbc-muted">{b.preview.suppressed_count} opted-out contact{b.preview.suppressed_count !== 1 ? 's' : ''} left out (POPIA).</p>)}
          {n === 0
            ? <p className="vbc-error" role="alert">No recipients in this audience: nothing to send.</p>
            : <div className="vbc-actions">
                <Button variant="secondary" Icon={X} onClick={() => b.setPreview(null)}>Cancel</Button>
                <Button Icon={Send} disabled={b.sending} onClick={b.sendBroadcast}>{b.sending ? 'Sending…' : `Send live to ${n}`}</Button>
              </div>}
        </div>
      )}
    </Card>
  )
}

const segHint = (cr = {}) => [
  cr.not_ordered_within_days != null && `no order in ${cr.not_ordered_within_days} days`,
  cr.ordered_within_days != null && `ordered within ${cr.ordered_within_days} days`,
  cr.min_spend != null && `spend ≥ ${formatMoney(cr.min_spend * 100)}`,
  cr.min_orders != null && `≥ ${cr.min_orders} orders`,
  cr.channel && cr.channel,
  cr.tags?.length && `tags: ${cr.tags.join(', ')}`,
  cr.area && `area: ${cr.area}`,
  cr.language && (SEG_LANGUAGES[cr.language] || cr.language),
].filter(Boolean).join(' · ')

function Audience({ b }) {
  const f = b.segForm
  const set = (k) => (e) => b.setSegForm({ ...f, [k]: e.target.value })
  const row = (id, label, hint, extra) => {
    const n = b.audCount(id)
    return (
      <li key={id} className="vbc-aud">
        <label className="vbc-check">
          <input type="checkbox" checked={b.audiences.includes(id)} onChange={() => b.toggleAud(id)} />
          <span className="vbc-grow"><b>{label}</b><span className="vbc-muted">{hint}</span></span>
        </label>
        {n != null && <StatusChip tone="accent">{n} reachable</StatusChip>}
        {extra}
      </li>)
  }
  return (
    <fieldset className="vbc-fieldset">
      <legend className="vbc-legend"><Users size={16} aria-hidden="true" />Audience <span className="vbc-muted">Pick one or more; overlaps are counted once.</span></legend>
      <ul className="vbc-auds">
        {AUDIENCES.map(a => row(a.id, a.label, a.hint))}
        {b.segments.map(seg => row(`seg:${seg.id}`, seg.name, segHint(seg.criteria),
          <IconButton quiet Icon={Trash2} className="vbc-danger" label={`Delete segment ${seg.name}`} onClick={() => b.deleteSegment(seg.id)} />))}
      </ul>
      <div><Button variant="secondary" size="sm" Icon={b.showSeg ? X : Plus} aria-expanded={b.showSeg} onClick={() => b.setShowSeg(v => !v)}>
        {b.showSeg ? 'Close' : 'New segment'}</Button></div>
      {b.showSeg && (
        <form className="vbc-seg" onSubmit={e => { e.preventDefault(); b.createSegment() }}>
          <Field label="Segment name"><Input value={f.name} onChange={set('name')} /></Field>
          <Field label="No order in … days"><Input type="number" min="0" value={f.not_ordered_within_days} onChange={set('not_ordered_within_days')} /></Field>
          <Field label="Spent at least (R)"><Input type="number" min="0" value={f.min_spend} onChange={set('min_spend')} /></Field>
          <Field label="Channel">
            <Select value={f.channel} onChange={set('channel')}><option value="">Any channel</option><option value="whatsapp">WhatsApp</option><option value="web">Web</option></Select>
          </Field>
          <Field label="Tags" hint="Comma-separated; matches any. Set tags per customer in Customers."><Input value={f.tags} onChange={set('tags')} /></Field>
          <Field label="Area"><Input value={f.area} onChange={set('area')} /></Field>
          <Field label="Language">
            <Select value={f.language} onChange={set('language')}>
              <option value="">Any language</option>
              {Object.entries(SEG_LANGUAGES).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </Select>
          </Field>
          <div className="vbc-end"><Button type="submit" Icon={Plus}>Save segment</Button></div>
        </form>)}
    </fieldset>
  )
}

// ── Schedule ──────────────────────────────────────────────────────────────────

function Schedule({ b }) {
  return (
    <Card className="vbc-card" role="group" aria-labelledby="vbc-sched">
      <h2 className="vbc-h" id="vbc-sched"><CalendarClock size={18} aria-hidden="true" />Schedule for later</h2>
      <p className="vbc-muted">Uses the message and audience above; a repeating one repeats from this time. Food promotions land best on weekday late afternoons (about 4–6pm), before dinner planning.</p>
      <form className="vbc-row vbc-bottom" onSubmit={e => { e.preventDefault(); b.scheduleCampaign() }}>
        <Field label="When" style={{ flex: '1 1 200px', minWidth: 0 }}>
          <Input type="datetime-local" value={b.scheduleAt} onChange={e => b.setScheduleAt(e.target.value)} />
        </Field>
        <Field label="Repeats" style={{ flex: '1 1 140px', minWidth: 0 }}>
          <Select value={b.recurrence} onChange={e => b.setRecurrence(e.target.value)}>
            <option value="once">One-off</option><option value="daily">Every day</option>
            <option value="weekly">Every week</option><option value="monthly">Every month</option>
          </Select>
        </Field>
        <Button type="submit" Icon={CalendarClock} disabled={b.sending}>{b.sending ? 'Scheduling…' : 'Schedule'}</Button>
      </form>
      {b.campaigns.length > 0 && (
        <ul className="vbc-list" aria-label="Scheduled broadcasts">
          {b.campaigns.map(c => (
            <li key={c.id} className="vbc-item">
              <span className="vbc-grow"><b>{plain(c.name)}</b>
                <span className="vbc-muted">{c.audience_filter} · {c.recurrence} · next {when(c.next_run_at, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span></span>
              <Button variant="secondary" size="sm" Icon={X} aria-label={`Cancel ${plain(c.name)}`} onClick={() => b.deleteCampaign(c.id)}>Cancel</Button>
            </li>))}
        </ul>)}
    </Card>
  )
}

// ── Analytics + history ───────────────────────────────────────────────────────

function Analytics({ analytics }) {
  const t = analytics.totals || {}
  const best = analytics.best_performing || []
  const trend = analytics.trend || []
  const max = Math.max(1, ...trend.map(w => w.sent))
  return (
    <Card className="vbc-card" role="group" aria-labelledby="vbc-analytics">
      <h2 className="vbc-h" id="vbc-analytics"><BarChart3 size={18} aria-hidden="true" />Marketing analytics <span className="vbc-muted">last {analytics.days} days</span></h2>
      <div className="vbc-tiles">
        <StatCard variant="tile" label="Broadcasts sent" value={t.broadcasts} />
        <StatCard variant="tile" label="Total reach" value={t.sent} />
        <StatCard variant="tile" label="Open rate" value={`${t.open_rate}%`} />
        <StatCard variant="tile" label="Click rate" value={`${t.click_rate}%`} />
        <StatCard variant="tile" label="Attributed revenue" value={money(t.attributed_revenue_cents)}
          sub={t.attributed_orders ? `${t.attributed_orders} order${t.attributed_orders !== 1 ? 's' : ''}` : undefined} />
      </div>
      {trend.length > 1 && (
        <figure className="vbc-trend">
          <figcaption className="vbc-h4">Weekly reach</figcaption>
          <ol className="vbc-bars">
            {trend.map(w => (
              <li key={w.week} title={`Week of ${w.week}: ${w.sent} sent, ${w.clicked} clicked`} aria-label={`Week of ${w.week}: ${w.sent} sent, ${w.clicked} clicked`}
                style={{ height: `${Math.max(6, Math.round(100 * w.sent / max))}%` }} />))}
          </ol>
        </figure>)}
      {best.length > 0 && (
        <>
          <h3 className="vbc-h4">Best performing</h3>
          <ol className="vbc-best">
            {best.map(x => <li key={x.id}><span className="vbc-grow">{x.name}</span><b>{x.click_rate}% clicked</b></li>)}
          </ol>
        </>)}
    </Card>
  )
}

function History({ b, tenantId }) {
  return (
    <section className="vbc" aria-labelledby="vbc-history">
      <h2 className="vbc-h" id="vbc-history">Broadcast history</h2>
      {b.loading && !b.broadcasts.length ? <p className="vbc-muted">Loading…</p>
        : b.broadcasts.length === 0
          ? <EmptyState Icon={Megaphone} title="No broadcasts yet">Create your first one above.</EmptyState>
          : <ul className="vbc-list vbc-history">
              {b.broadcasts.map(x => {
                const open = b.funnelId === x.id
                return (
                  <li key={x.id}>
                    <Card pad={0} className="vbc-hcard">
                      <button type="button" className="vbc-hrow" aria-expanded={open} onClick={() => b.setFunnelId(open ? null : x.id)}>
                        <span className="vbc-grow"><b>{plain(x.name || x.template_name)}</b>
                          <span className="vbc-muted">{x.audience_filter} · {x.status} · {when(x.created_at, { day: 'numeric', month: 'short', year: 'numeric' })}</span></span>
                        <span className="vbc-stats">
                          {x.sent_count > 0 && <span><Send size={13} aria-hidden="true" />{x.sent_count}<span className="vbc-sr"> sent</span></span>}
                          {x.read_count > 0 && <span><Eye size={13} aria-hidden="true" />{x.read_count}<span className="vbc-sr"> read</span></span>}
                          {x.clicked_count > 0 && <span><MousePointerClick size={13} aria-hidden="true" />{x.clicked_count}<span className="vbc-sr"> clicked</span></span>}
                        </span>
                        <ChevronDown size={18} aria-hidden="true" className="vbc-chev" />
                      </button>
                      {open && <Funnel tenantId={tenantId} broadcastId={x.id} />}
                    </Card>
                  </li>)
              })}
            </ul>}
    </section>
  )
}

function Funnel({ tenantId, broadcastId }) {
  const data = useBroadcastFunnel(tenantId, broadcastId)
  if (!data) return <p className="vbc-muted vbc-funnel">Loading…</p>
  const f = data.funnel || {}
  const max = Math.max(1, f.sent || 1)
  const stages = [['Sent', f.sent || 0, 'muted'], ['Delivered', f.delivered || 0, 'brand'], ['Read', f.read || 0, 'brand'], ['Clicked', f.clicked || 0, 'brand'],
    ...(f.failed ? [['Failed', f.failed, 'danger']] : [])]
  const clickers = (data.recipients || []).filter(r => r.clicked_at)
  const failures = (data.recipients || []).filter(r => r.status === 'failed')
  return (
    <div className="vbc-funnel">
      <dl className="vbc-stages">
        {stages.map(([label, n, tone]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd><span className="vbc-meter" aria-hidden="true"><span className={`vbc-fill vbc-fill--${tone}`} style={{ width: `${Math.round(100 * n / max)}%` }} /></span><b>{n}</b></dd>
          </div>))}
      </dl>
      {f.attributed_orders > 0 && (
        <p className="vbc-ok">{money(f.attributed_revenue_cents)} from {f.attributed_orders} order{f.attributed_orders !== 1 ? 's' : ''} <span className="vbc-muted">placed within 7 days of a click</span></p>)}
      {clickers.length > 0 && <p className="vbc-muted">Clicked: {clickers.slice(0, 8).map(r => r.phone).join(', ')}{clickers.length > 8 ? ` +${clickers.length - 8} more` : ''}</p>}
      {failures.length > 0 && <p className="vbc-error">Failed: {failures.slice(0, 5).map(r => `${r.phone}${r.error ? ` (${String(r.error).slice(0, 30)})` : ''}`).join(', ')}{failures.length > 5 ? ` +${failures.length - 5}` : ''}</p>}
    </div>
  )
}
