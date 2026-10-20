/**
 * EmailCampaignsV2.jsx — the redesigned Marketing › Email (2026-10-20, UX redesign Phase F). Same
 * calls and the same two steps as VulaEmailCampaigns (broadcast/useEmailCampaigns.js): write the
 * email, preview who it reaches (a dry run that sends nothing), then "Send live to N".
 * Unsubscribed contacts are left out and every email carries an unsubscribe link (server-side).
 */
import { AlertTriangle, Eye, Mail, Send, Users, X } from 'lucide-react'
import { SA_TZ } from '../../lib/dates'
import { Button, Card, EmptyState, Field, Input, Textarea } from '../ui/index.jsx'
import { EMAIL_AUDIENCES, useEmailCampaigns } from './useEmailCampaigns'
import './broadcast.css'

const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+\s*/, '')
const day = (iso) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric', timeZone: SA_TZ })
}

export default function EmailCampaignsV2({ tenantId }) {
  const x = useEmailCampaigns(tenantId)
  const n = x.preview?.recipient_count
  const auds = [...EMAIL_AUDIENCES.map(a => [a.id, a.label]), ...x.segments.map(sg => [`seg:${sg.id}`, sg.name])]
  return (
    <section className="vbc" aria-label="Email campaigns">
      <p className="vbc-sub">Send a one-off email to a group of customers from your connected mailbox. Every email includes an unsubscribe link.</p>
      <Card className="vbc-card" role="group" aria-labelledby="vem-compose">
        <h2 className="vbc-h" id="vem-compose"><Mail size={18} aria-hidden="true" />New email</h2>
        <Field label="Subject"><Input value={x.subject} onChange={e => x.setSubject(e.target.value)} placeholder="e.g. This week's specials" /></Field>
        <Field label="Message" hint="Plain text. This is exactly what customers receive, plus an unsubscribe footer.">
          <Textarea rows={8} value={x.bodyText} onChange={e => x.setBodyText(e.target.value)} />
        </Field>
        <form className="vbc-row vbc-bottom" onSubmit={e => { e.preventDefault(); x.sendTest() }}>
          <Field label="Send a test to" style={{ flex: '1 1 220px', minWidth: 0 }}>
            <Input type="email" value={x.testEmail} onChange={e => x.setTestEmail(e.target.value)} placeholder="you@example.com" />
          </Field>
          <Button type="submit" variant="secondary" Icon={Send}>Send test</Button>
        </form>
        {x.testMsg && <p className={x.testMsg.startsWith('✓') ? 'vbc-ok' : 'vbc-error'} role="status">{plain(x.testMsg)}</p>}

        <fieldset className="vbc-fieldset">
          <legend className="vbc-legend"><Users size={16} aria-hidden="true" />Audience <span className="vbc-muted">Pick one or more; overlaps are counted once.</span></legend>
          <ul className="vbc-auds">
            {auds.map(([id, label]) => (
              <li key={id} className="vbc-aud">
                <label className="vbc-check"><input type="checkbox" checked={x.audiences.includes(id)} onChange={() => x.toggleAud(id)} />
                  <span className="vbc-grow"><b>{label}</b></span></label>
              </li>))}
          </ul>
        </fieldset>

        {x.error && <p className="vbc-error" role="alert">{x.error}</p>}
        {x.sent && <p className="vbc-ok" role="status">Sent to {x.sent.sent} recipient{x.sent.sent !== 1 ? 's' : ''}{x.sent.failed ? ` · ${x.sent.failed} failed` : ''}.</p>}
        <div className="vbc-summary"><span className="vbc-muted">About to send to</span><b>{plain(x.selectedAudLabel)}</b></div>

        {!x.preview ? (
          <div className="vbc-actions vbc-actions--sticky">
            <Button Icon={Eye} disabled={x.sending} onClick={x.previewCampaign}>{x.sending ? 'Checking…' : 'Preview audience'}</Button>
          </div>
        ) : (
          <div className="vbc-confirm" role="group" aria-label="Confirm the email">
            <p className="vbc-body">This will email <b>{n}</b> customer{n !== 1 ? 's' : ''}
              {x.preview.sample?.length > 0 && <span className="vbc-muted"> (e.g. {x.preview.sample.slice(0, 3).map(r => r.email).join(', ')}{n > 3 ? '…' : ''})</span>}</p>
            {x.preview.suppressed_count > 0 && <p className="vbc-muted">{x.preview.suppressed_count} unsubscribed contact{x.preview.suppressed_count !== 1 ? 's' : ''} left out.</p>}
            {n === 0
              ? <p className="vbc-error" role="alert">No recipients with an email address in this audience: nothing to send.</p>
              : <div className="vbc-actions">
                  <Button variant="secondary" Icon={X} onClick={() => x.setPreview(null)}>Cancel</Button>
                  <Button Icon={Send} disabled={x.sending} onClick={x.sendCampaign}>{x.sending ? 'Sending…' : `Send live to ${n}`}</Button>
                </div>}
          </div>)}
      </Card>

      <section className="vbc" aria-labelledby="vem-history">
        <h2 className="vbc-h" id="vem-history">Campaign history</h2>
        {x.loading && !x.campaigns.length ? <p className="vbc-muted">Loading…</p>
          : x.campaigns.length === 0
            ? <EmptyState Icon={Mail} title="No email campaigns yet">Create your first one above.</EmptyState>
            : <ul className="vbc-list">
                {x.campaigns.map(c => (
                  <li key={c.id}>
                    <Card className="vbc-row">
                      <span className="vbc-grow"><b>{c.name || c.subject}</b>
                        <span className="vbc-muted">{c.audience_filter} · {c.status} · {day(c.created_at)}</span></span>
                      <span className="vbc-stats">
                        {c.sent_count > 0 && <span><Send size={13} aria-hidden="true" />{c.sent_count}<span className="vbc-sr"> sent</span></span>}
                        {c.failed_count > 0 && <span className="vbc-danger"><AlertTriangle size={13} aria-hidden="true" />{c.failed_count}<span className="vbc-sr"> failed</span></span>}
                      </span>
                    </Card>
                  </li>))}
              </ul>}
      </section>
    </section>
  )
}
