/**
 * EmailConnectV2.jsx — the redesigned email (IMAP / SMTP) connection in Settings (2026-10-19, UX
 * redesign Phase F). Same data and actions as VulaEmailConnect (connect/useEmailConnect.js): one
 * row per mailbox (primary, draft-only or sending, last sync) with Make primary / Backfill /
 * Disconnect, and the connect form with every field labelled and the host presets.
 */
import { Link2, Plus, Star, Unplug } from 'lucide-react'
import { SA_TZ } from '../../../lib/dates'
import { Button, Card, Field, Input, Select, Skeleton, StatusChip } from '../../ui/index.jsx'
import { PRESETS, blank, useEmailConnect } from './useEmailConnect'
import '../settings.css'

const when = (iso) => new Date(iso).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: SA_TZ })

export default function EmailConnectV2({ tenantId, adminEmail }) {
  const x = useEmailConnect({ tenantId, adminEmail })
  const f = x.form
  const set = (patch) => x.setForm({ ...f, ...patch })
  return (
    <Card className="vst-card">
      <div className="vst-row">
        <span className="vst-label">Status</span>
        <StatusChip tone={x.accounts.length ? 'ok' : 'muted'}>{x.accounts.length ? `${x.accounts.length} connected` : 'None connected'}</StatusChip>
      </div>
      <p className="vst-sub">Connect as many mailboxes as you need — admin@, sales@, a personal Gmail…</p>

      {x.loading ? <Skeleton lines={2} /> : x.accounts.length > 0 && (
        <ul className="vcn-list" aria-label="Connected mailboxes">
          {x.accounts.map(a => (
            <li key={a.id} className="vcn-item">
              <div className="vcn-item-main">
                <span className="vst-row"><span className="vst-strong">{a.email}</span>{a.is_primary && <StatusChip tone="highlight">Primary</StatusChip>}</span>
                <span className="vst-sub">
                  {a.send_mode === 'send' ? 'Sends directly' : 'Draft-only (you send)'}
                  {a.last_sync_at ? ` · last synced ${when(a.last_sync_at)}` : ' · not yet synced'}
                </span>
              </div>
              <div className="vst-row">
                {!a.is_primary && <Button size="sm" variant="secondary" Icon={Star} disabled={x.busyAccountId === a.id}
                  onClick={() => x.setPrimary(a.id)} aria-label={`Make ${a.email} the primary mailbox`}>Make primary</Button>}
                <Button size="sm" variant="secondary" disabled={x.busyAccountId === a.id}
                  onClick={() => x.backfill(a.id)} aria-label={`Backfill ${a.email}`}>Backfill</Button>
                <Button size="sm" variant="quiet" Icon={Unplug} className="vcn-danger-btn" disabled={x.busyAccountId === a.id}
                  onClick={() => x.disconnect(a.id)} aria-label={`Disconnect ${a.email}`}>Disconnect</Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {!x.showForm ? (
        <div className="vst-row">
          <Button Icon={x.accounts.length ? Plus : Link2} onClick={() => x.setShowForm(true)}>
            {x.accounts.length ? 'Connect another mailbox' : 'Connect a mailbox'}
          </Button>
        </div>
      ) : (
        <form className="vst-card vcn-token" onSubmit={(e) => { e.preventDefault(); x.connect() }}>
          <p className="vst-body">Use an <strong>app password</strong> (not your main login) where your provider supports it — it's revocable and scoped. Gmail requires one if 2FA is on.</p>
          <Field label="Quick setup" hint="Fills in the hosts and ports for common providers.">
            <Select defaultValue="" onChange={e => x.applyPreset(e.target.value)}>
              <option value="" disabled>Choose a provider…</option>
              {Object.keys(PRESETS).map(p => <option key={p} value={p}>{p}</option>)}
            </Select>
          </Field>
          <div className="vst-grid">
            <Field label="Email address"><Input type="email" autoComplete="off" value={f.email} onChange={e => set({ email: e.target.value })} /></Field>
            <Field label="Password / app password"><Input type="password" autoComplete="new-password" value={f.password} onChange={e => set({ password: e.target.value })} /></Field>
          </div>
          <div className="vcn-host">
            <Field label="IMAP host"><Input value={f.imap_host} onChange={e => set({ imap_host: e.target.value })} /></Field>
            <Field label="IMAP port"><Input inputMode="numeric" value={f.imap_port} onChange={e => set({ imap_port: e.target.value })} /></Field>
          </div>
          <div className="vcn-host">
            <Field label="SMTP host (for sending)"><Input value={f.smtp_host} onChange={e => set({ smtp_host: e.target.value })} /></Field>
            <Field label="SMTP port"><Input inputMode="numeric" value={f.smtp_port} onChange={e => set({ smtp_port: e.target.value })} /></Field>
          </div>
          <div className="vst-grid">
            <Field label="From name (optional)"><Input value={f.from_name} onChange={e => set({ from_name: e.target.value })} /></Field>
            <Field label="Sending">
              <Select value={f.send_mode} onChange={e => set({ send_mode: e.target.value })}>
                <option value="draft">Draft-only</option><option value="send">Send directly</option>
              </Select>
            </Field>
          </div>
          {x.error && <p className="vcn-error" role="alert">{x.error}</p>}
          <div className="vst-row">
            <Button type="submit" Icon={Link2} disabled={x.busy}>{x.busy ? 'Testing connection…' : 'Connect mailbox'}</Button>
            <Button type="button" variant="secondary" onClick={() => { x.setShowForm(false); x.setError(null); x.setForm(blank) }}>Cancel</Button>
          </div>
        </form>
      )}
    </Card>
  )
}
