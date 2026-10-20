/**
 * VulaEmailCampaigns.jsx — bulk/campaign email, distinct from the 1:1 business
 * correspondence in the Assistant's email skill. Reuses the exact same audience
 * targeting (built-in filters + saved segments) as WhatsApp Broadcast — a customer's
 * email is already merged into the same aggregated customer list.
 */
import { EMAIL_AUDIENCES as AUDIENCES, useEmailCampaigns } from './broadcast/useEmailCampaigns'
import EmailCampaignsV2 from './broadcast/EmailCampaignsV2'

export default function VulaEmailCampaigns({ v2 = false, ...props }) {
  return v2 ? <EmailCampaignsV2 {...props} /> : <EmailCampaignsV1 {...props} />
}

function EmailCampaignsV1({ tenantId }) {
  const { campaigns, segments, loading, subject, setSubject, bodyText, setBodyText, audiences, toggleAud, testEmail, setTestEmail, testMsg, sending, preview, setPreview, sent, error, sendTest, previewCampaign, sendCampaign, selectedAudLabel } = useEmailCampaigns(tenantId)

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
          {testMsg && <span style={{ fontSize: 12, color: testMsg.startsWith('✓') ? 'var(--ok)' : 'var(--danger)' }}>{testMsg}</span>}
        </div>

        <p style={s.sectionLabel}>Audience <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— pick one or more; overlaps de-duplicated</span></p>
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
                {c.failed_count > 0 && <span style={{ ...s.statChip, color: 'var(--danger)' }}>⚠ {c.failed_count}</span>}
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
  h3:           { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:          { fontSize: 13, color: 'var(--muted)', margin: 0, lineHeight: 1.5 },
  composeCard:  { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 18, marginBottom: 20 },
  sectionLabel: { fontSize: 12, fontWeight: 600, color: 'var(--ink)', margin: '16px 0 8px' },
  input:        { padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 14, outline: 'none', boxSizing: 'border-box' },
  textarea:     { width: '100%', padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 14, outline: 'none', boxSizing: 'border-box', resize: 'vertical' },
  aiBtn:        { padding: '10px 16px', background: 'var(--warn)', color: 'var(--bg)', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' },
  audRow:       { display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 },
  audBtn:       { background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 20, padding: '6px 12px', cursor: 'pointer', textAlign: 'left', fontSize: 12.5, color: 'var(--ink)' },
  audBtnActive: { background: 'rgba(44,85,69,0.08)', border: '1px solid var(--accent, var(--accent))' },
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
  cancelBtn:    { padding: '10px 16px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 13, cursor: 'pointer', color: 'var(--muted)' },
  sendLiveBtn:  { flex: 1, padding: '10px 16px', background: 'var(--ok)', color: 'var(--bg)', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer'},
  muted:        { color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '24px 0' },
  histList:     { display: 'flex', flexDirection: 'column', gap: 6 },
  histRow:      { display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' },
  histName:     { display: 'block', fontSize: 13.5, fontWeight: 600, color: 'var(--ink)' },
  histMeta:     { display: 'block', fontSize: 11, color: 'var(--muted)' },
  histStats:    { display: 'flex', gap: 6, alignItems: 'center' },
  statChip:     { fontFamily: 'monospace', fontSize: 11.5, color: 'var(--muted)', background: 'var(--bg)', padding: '2px 7px', borderRadius: 10 },
}
