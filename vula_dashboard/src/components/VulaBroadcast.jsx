/**
 * VulaBroadcast.jsx — WhatsApp broadcast / campaign builder.
 *
 * Send promotional messages to customers via approved Meta templates.
 * Audience filters: all / ordered last 30 days / high value (>R500).
 * History shows sent/delivered/read counts.
 */

import { TEMPLATES, AUDIENCES, SEG_LANGUAGES, usePicturePromo, useBroadcast, useBroadcastFunnel } from './broadcast/useBroadcast'
import BroadcastV2 from './broadcast/BroadcastV2'

// 📸 Picture promotion (2026-10-13): a product photo, the offer and a Shop now button — the
// shop's approved promotion template, sent to the audience through the normal broadcast path.
function PicturePromo({ tenantId }) {
  const { products, productId, setProductId, message, setMessage, audience, setAudience, testPhone, setTestPhone, preview, setPreview, busy, note, chosen, doPreview, doTest, doSend, approved } = usePicturePromo(tenantId)

  return (
    <div style={{ ...s.composeCard, marginBottom: 16 }}>
      <p style={{ ...s.sectionLabel, marginTop: 0 }}>📸 Picture promotion</p>
      <p style={{ ...s.sub, marginTop: 0 }}>A product photo, your offer and a <b>Shop now</b> button — reaches customers even if they haven't messaged lately.</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 14, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <select value={productId} onChange={e => { setProductId(e.target.value); setPreview(null) }} style={{ ...s.input, fontSize: 16 }}>
            <option value="">Photo: first product in the shop</option>
            {products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <textarea value={message} onChange={e => { setMessage(e.target.value); setPreview(null) }} rows={3} maxLength={500}
            placeholder="Your offer, e.g. 15% off all basins this weekend only!" style={{ ...s.input, fontSize: 16, resize: 'vertical' }} />
          <select value={audience} onChange={e => { setAudience(e.target.value); setPreview(null) }} style={{ ...s.input, fontSize: 16 }}>
            {AUDIENCES.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button disabled={busy || !message.trim()} onClick={doPreview} style={{ ...s.tplBtn, flex: 1, textAlign: 'center' }}>Preview</button>
            <input value={testPhone} onChange={e => setTestPhone(e.target.value)} placeholder="Your number" style={{ ...s.input, flex: '1 1 120px', fontSize: 16 }} />
            <button disabled={busy || !message.trim() || !approved} onClick={doTest} style={{ ...s.tplBtn, textAlign: 'center' }}>Test to me</button>
          </div>
          {preview && (
            <p style={s.sub}>
              Reaches <b>{preview.recipient_count}</b> customer{preview.recipient_count === 1 ? '' : 's'}
              {preview.suppressed_count ? ` (${preview.suppressed_count} opted out, left out)` : ''}.
              {!approved && <> The promotion template is <b>{String(preview.template_status || '').toLowerCase()}</b> — submit the standard templates under Settings › WhatsApp templates; Meta usually approves within a day.</>}
            </p>
          )}
          <button disabled={busy || !preview || !approved || !preview.recipient_count} onClick={() => doSend()}
            style={busy || !preview || !approved || !preview.recipient_count ? s.btnDisabled : { ...s.sendBtn, color: 'var(--on-accent)' }}>
            Send promotion
          </button>
          {note && <p style={{ ...s.sub, color: note.ok ? 'var(--ok)' : 'var(--danger)' }}>{note.text}</p>}
        </div>
        {/* How it looks in WhatsApp */}
        <div style={{ background: 'var(--surface-alt)', borderRadius: 14, padding: 10 }}>
          <div style={{ background: 'var(--surface)', borderRadius: 10, overflow: 'hidden', boxShadow: 'var(--shadow-sm)' }}>
            {(preview?.image_url || chosen?.image_url || products[0]?.image_url) && (
              <img src={preview?.image_url || chosen?.image_url || products[0]?.image_url} alt=""
                style={{ width: '100%', aspectRatio: '1.6', objectFit: 'cover', display: 'block' }} />
            )}
            <p style={{ margin: 0, padding: '8px 10px', fontSize: 13.5, color: 'var(--text)', whiteSpace: 'pre-wrap' }}>
              Hi Thandi! {message.trim() || 'Your offer goes here.'} Tap Shop now to have a look. Reply STOP if you'd rather not get offers.
            </p>
            <div style={{ borderTop: '1px solid var(--border)', padding: 9, textAlign: 'center', color: 'var(--info)', fontWeight: 600, fontSize: 13.5 }}>↗ Shop now</div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function VulaBroadcast({ v2 = false, ...props }) {
  return v2 ? <BroadcastV2 {...props} /> : <BroadcastV1 {...props} />
}

function BroadcastV1({ tenantId, draftBody, onConsumeDraft }) {
  const {
    analytics, approvedName, approvedTpl, audCount, audiences, bodyText, broadcasts, campaigns, createSegment, deleteCampaign, deleteSegment, details, drafting, error, funnelId, headerImageUrl, loading, preview, previewBroadcast, realTemplates, recurrence, scheduleAt, scheduleCampaign, segForm, segHint, segments, selectedAudLabel, selectedTpl, sendBroadcast, sendTest, sending, sent, setApprovedName, setBodyText, setDetails, setFunnelId, setHeaderImageUrl, setPreview, setRecurrence, setScheduleAt, setSegForm, setShowSeg, setTargetUrl, setTemplate, setTestPhone, setUseApproved, showSeg, targetUrl, template, testMsg, testPhone, toggleAud, useApproved, writeWithAI,
  } = useBroadcast(tenantId, draftBody, onConsumeDraft)

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>📢 WhatsApp Broadcasts</h3>
        <p style={s.sub}>Send promotional messages to your customers via approved WhatsApp templates.</p>
      </div>

      <PicturePromo tenantId={tenantId} />

      {/* Compose */}
      <div style={s.composeCard}>
        {/* Free text (within 24h of a customer's last message) vs. an approved template
            (works cold, and can carry a header image / buttons — see 📨 Templates tab) */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <button onClick={() => setUseApproved(false)} style={{ ...s.tplBtn, flex: 1, ...(!useApproved ? s.tplBtnActive : {}) }}>
            <span style={s.tplLabel}>💬 Free text</span>
            <span style={s.tplHint}>Draft &amp; preview — sending needs an approved template</span>
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
          {testMsg && <span style={{ fontSize: 12, color: testMsg.startsWith('✓') ? 'var(--ok)' : 'var(--danger)' }}>{testMsg}</span>}
        </div>

        <p style={s.sectionLabel}>Audience <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— pick one or more; overlaps are de-duplicated</span></p>
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
                  style={{ marginLeft: 6, color: 'var(--danger)', cursor: 'pointer' }}>×</span>
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
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12 }} />
            <input type="number" placeholder="no order in … days" value={segForm.not_ordered_within_days}
              onChange={e => setSegForm({ ...segForm, not_ordered_within_days: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12, width: 150 }} />
            <input type="number" placeholder="min spend (R)" value={segForm.min_spend}
              onChange={e => setSegForm({ ...segForm, min_spend: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12, width: 120 }} />
            <select value={segForm.channel} onChange={e => setSegForm({ ...segForm, channel: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12 }}>
              <option value="">any channel</option><option value="whatsapp">WhatsApp</option><option value="web">Web</option>
            </select>
            <input placeholder="tags (comma-separated)" value={segForm.tags}
              onChange={e => setSegForm({ ...segForm, tags: e.target.value })}
              title="Matches a customer with ANY of these tags — set tags per-customer in the Customers tab"
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12, width: 170 }} />
            <input placeholder="area" value={segForm.area}
              onChange={e => setSegForm({ ...segForm, area: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12, width: 110 }} />
            <select value={segForm.language} onChange={e => setSegForm({ ...segForm, language: e.target.value })}
              style={{ padding: '7px 9px', border: '1px solid var(--border)', borderRadius: 7, fontSize: 12 }}>
              <option value="">any language</option>
              {Object.entries(SEG_LANGUAGES).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </select>
            <button onClick={createSegment} style={s.sendBtn}>Save segment</button>
          </div>
        )}

        {error && <p style={s.error}>{error}</p>}
        {sent && (
          <p style={s.success}>
            {sent.queued
              ? `✓ Sending to ${sent.recipient_count} recipients in the background (about ${Math.ceil(sent.recipient_count / 600)} min) — progress shows in the history below.`
              : <>✓ Sent to {sent.sent} recipient{sent.sent !== 1 ? 's' : ''}{sent.failed ? ` · ${sent.failed} failed` : ''}.</>}
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
          style={{ padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 13 }} />
        <select value={recurrence} onChange={e => setRecurrence(e.target.value)}
          style={{ padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 13 }}>
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

      {/* Aggregated analytics — rolls up every campaign in the window (default 90 days) */}
      {analytics && analytics.totals?.broadcasts > 0 && (
        <>
          <p style={s.sectionLabel}>📊 Marketing analytics <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— last {analytics.days} days</span></p>
          <BroadcastAnalytics analytics={analytics} />
        </>
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
                  <span style={{ ...s.statChip, color: 'var(--muted)' }}>{funnelId === b.id ? '▴' : '▾'}</span>
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

function BroadcastAnalytics({ analytics }) {
  const t = analytics.totals || {}
  const best = analytics.best_performing || []
  const trend = analytics.trend || []
  const maxTrend = Math.max(1, ...trend.map(w => w.sent))
  const tiles = [
    { label: 'Broadcasts sent', value: t.broadcasts },
    { label: 'Total reach', value: t.sent },
    { label: 'Open rate', value: `${t.open_rate}%` },
    { label: 'Click rate', value: `${t.click_rate}%` },
    { label: 'Attributed revenue', value: `R${((t.attributed_revenue_cents || 0) / 100).toFixed(2)}`,
      hint: t.attributed_orders ? `${t.attributed_orders} order${t.attributed_orders !== 1 ? 's' : ''}` : null },
  ]
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 18 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, marginBottom: trend.length > 1 ? 16 : 0 }}>
        {tiles.map(tile => (
          <div key={tile.label} style={{ background: 'var(--bg)', borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', color: 'var(--muted)', fontFamily: "'Source Code Pro', monospace", marginBottom: 3 }}>{tile.label}</div>
            <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--accent)' }}>{tile.value}</div>
            {tile.hint && <div style={{ fontSize: 10.5, color: 'var(--muted)' }}>{tile.hint}</div>}
          </div>
        ))}
      </div>

      {trend.length > 1 && (
        <div style={{ marginBottom: best.length > 0 ? 16 : 0 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', fontFamily: "'Source Code Pro', monospace", marginBottom: 6 }}>Weekly reach</div>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 48 }}>
            {trend.map(w => (
              <div key={w.week} title={`Week of ${w.week}: ${w.sent} sent, ${w.clicked} clicked`}
                style={{ flex: 1, height: `${Math.max(4, Math.round(44 * w.sent / maxTrend))}px`,
                  background: 'var(--accent)', borderRadius: 3, opacity: 0.75 }} />
            ))}
          </div>
        </div>
      )}

      {best.length > 0 && (
        <div>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', fontFamily: "'Source Code Pro', monospace", marginBottom: 6 }}>Best performing</div>
          {best.map((b, i) => (
            <div key={b.id} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '4px 0', alignItems: 'center' }}>
              <span style={{ color: 'var(--muted)', width: 14 }}>{i + 1}.</span>
              <span style={{ flex: 1, color: 'var(--ink)' }}>{b.name}</span>
              <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{b.click_rate}% clicked</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function BroadcastFunnel({ tenantId, broadcastId }) {
  const data = useBroadcastFunnel(tenantId, broadcastId)
  if (!data) return <p style={{ fontSize: 12, color: 'var(--muted)', padding: '6px 12px' }}>Loading funnel…</p>
  const f = data.funnel || {}
  const stages = [
    { label: 'Sent', n: f.sent || 0, color: 'var(--muted)' },
    { label: 'Delivered', n: f.delivered || 0, color: 'var(--accent)' },
    { label: 'Read', n: f.read || 0, color: 'var(--accent)' },
    { label: 'Clicked', n: f.clicked || 0, color: 'var(--accent)' },
    ...(f.failed ? [{ label: 'Failed', n: f.failed, color: 'var(--danger)' }] : []),
  ]
  const max = Math.max(1, f.sent || 1)
  const clickers = (data.recipients || []).filter(r => r.clicked_at)
  const failures = (data.recipients || []).filter(r => r.status === 'failed')
  return (
    <div style={{ background: 'var(--surface-alt)', borderRadius: 8, padding: '10px 14px', margin: '2px 0 8px' }}>
      {stages.map(st => (
        <div key={st.label} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', fontSize: 12 }}>
          <span style={{ width: 66, color: 'var(--muted)' }}>{st.label}</span>
          <div style={{ flex: 1, height: 8, background: 'var(--surface)', borderRadius: 99, overflow: 'hidden' }}>
            <div style={{ width: `${Math.round(100 * st.n / max)}%`, height: '100%', background: st.color, borderRadius: 99 }} />
          </div>
          <span style={{ width: 34, textAlign: 'right', fontFamily: 'monospace', color: 'var(--ink)' }}>{st.n}</span>
        </div>
      ))}
      {f.attributed_orders > 0 && (
        <p style={{ fontSize: 12.5, color: 'var(--accent)', fontWeight: 600, margin: '10px 0 0' }}>
          💰 R{((f.attributed_revenue_cents || 0) / 100).toFixed(2)} from {f.attributed_orders} order{f.attributed_orders !== 1 ? 's' : ''}
          <span style={{ fontWeight: 400, color: 'var(--muted)' }}> — placed within 7 days of a click</span>
        </p>
      )}
      {clickers.length > 0 && (
        <p style={{ fontSize: 11.5, color: 'var(--muted)', margin: '8px 0 0' }}>
          🔗 Clicked: {clickers.slice(0, 8).map(r => r.phone).join(', ')}{clickers.length > 8 ? ` +${clickers.length - 8} more` : ''}
        </p>
      )}
      {failures.length > 0 && (
        <p style={{ fontSize: 11.5, color: 'var(--danger)', margin: '6px 0 0' }}>
          ⚠ Failed: {failures.slice(0, 5).map(r => `${r.phone}${r.error ? ` (${String(r.error).slice(0, 30)})` : ''}`).join(', ')}{failures.length > 5 ? ` +${failures.length - 5}` : ''}
        </p>
      )}
    </div>
  )
}

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:          { fontSize: 13, color: 'var(--muted)', margin: 0, lineHeight: 1.5 },
  composeCard:  { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 20 },
  sectionLabel: { fontSize: 12, fontWeight: 600, color: 'var(--ink)', margin: '16px 0 8px' },
  input:        { padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 14, outline: 'none', boxSizing: 'border-box' },
  textarea:     { width: '100%', padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 14, outline: 'none', boxSizing: 'border-box', resize: 'vertical' },
  aiBtn:        { padding: '10px 16px', background: 'var(--warn)', color: 'var(--bg)', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' },
  charCount:    { fontSize: 11, color: 'var(--muted)', margin: '4px 0 0', textAlign: 'right' },
  tplList:      { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 },
  tplBtn:       { background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 2 },
  tplBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, var(--accent))' },
  tplLabel:     { fontSize: 13, fontWeight: 600, color: 'var(--ink)' },
  tplHint:      { fontSize: 11, color: 'var(--muted)' },
  audRow:       { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 16 },
  audBtn:       { background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', gap: 10, alignItems: 'center' },
  audBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, var(--accent))' },
  audLabel:     { fontSize: 13, fontWeight: 600, color: 'var(--ink)', minWidth: 140 },
  audHint:      { fontSize: 11, color: 'var(--muted)' },
  reachBadge:   { marginLeft: 8, fontSize: 10, fontWeight: 600, color: 'var(--accent)', background: 'rgba(44,85,69,0.10)', padding: '1px 7px', borderRadius: 10 },
  preview:      { background: 'var(--bg)', borderRadius: 6, padding: '10px 14px', marginBottom: 14 },
  previewLabel: { fontSize: 11, color: 'var(--muted)', margin: '0 0 4px' },
  previewText:  { fontSize: 13, color: 'var(--ink)', margin: 0 },
  error:        { color: 'var(--danger)', fontSize: 13, margin: '0 0 10px' },
  success:      { color: 'var(--ok)', fontSize: 13, margin: '0 0 10px', fontWeight: 600 },
  sendBtn:      { width: '100%', padding: '12px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
  btnDisabled:  { width: '100%', padding: '12px', background: 'var(--border)', color: 'var(--muted)', border: 'none', borderRadius: 8, fontSize: 14, cursor: 'not-allowed'},
  confirmBox:   { background: 'rgba(37,211,102,0.06)', border: '1px solid rgba(37,211,102,0.3)', borderRadius: 8, padding: 14 },
  confirmText:  { fontSize: 13, color: 'var(--ink)', margin: '0 0 10px', lineHeight: 1.5 },
  sampleNames:  { color: 'var(--muted)' },
  confirmRow:   { display: 'flex', gap: 8 },
  cancelBtn:    { flex: 1, padding: '11px', background: 'transparent', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 14, cursor: 'pointer'},
  sendLiveBtn:  { flex: 2, padding: '11px', background: '#25D366', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
  muted:        { color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '16px 0' },
  histList:     { display: 'flex', flexDirection: 'column', gap: 6 },
  histRow:      { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10 },
  histName:     { display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--ink)' },
  histMeta:     { display: 'block', fontSize: 11, color: 'var(--muted)' },
  histStats:    { display: 'flex', gap: 6 },
  statChip:     { fontSize: 11, color: 'var(--muted)', background: 'var(--bg)', padding: '2px 8px', borderRadius: 10 },
}
