/**
 * YocoConnectV2.jsx — the redesigned Yoco payments connection in Settings (2026-10-19, UX redesign
 * Phase F). Same flow as VulaYocoConnect (connect/useYocoConnect.js): live or test mode, the
 * masked key, webhook state and last check, with Update keys / Test connection / Disconnect; or
 * the keys form, every field labelled.
 */
import { CheckCircle2, ExternalLink, KeyRound, Link2, Unplug } from 'lucide-react'
import { SA_TZ } from '../../../lib/dates'
import { Button, Field, Input } from '../../ui/index.jsx'
import { ConnectCard } from './ConnectCard.jsx'
import { useYocoConnect } from './useYocoConnect'

const when = (iso) => new Date(iso).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: SA_TZ })

export default function YocoConnectV2({ tenantId, tenantName, adminEmail }) {
  const x = useYocoConnect({ tenantId, tenantName, adminEmail })
  const connected = x.status === 'connected'
  const a = x.account || {}
  const live = a.mode === 'live'
  const rows = connected && x.account && !x.showForm ? [
    ['Mode', live ? 'Live — real payments' : 'Test — no real money moves', live ? 'ok' : 'warn'],
    ['Secret key', <code key="k" className="vcn-code">{a.masked_secret}</code>],
    ['Webhook', a.webhook_registered ? 'Registered' : 'Manual setup needed', a.webhook_registered ? 'ok' : 'warn'],
    a.last_test_at && ['Last verified', when(a.last_test_at)],
  ] : []
  const set = (patch) => x.setForm(f => ({ ...f, ...patch }))
  return (
    <ConnectCard status={x.status} label={connected ? (a.mode === 'test' ? 'Connected · test mode' : 'Connected · live') : undefined} rows={rows} error={x.error}>
      {x.showForm || !connected ? (
        <form className="vst-card" onSubmit={x.handleConnect}>
          <p className="vst-body">
            Get your keys from <a className="vcn-link" href="https://portal.yoco.com/dashboard/api-keys" target="_blank" rel="noopener noreferrer">portal.yoco.com › Developers › API Keys<ExternalLink size={14} aria-hidden="true" /></a>
          </p>
          <Field label="Secret key" hint="Starts with sk_live_ or sk_test_. Sent once, never shown again.">
            <Input type="password" required autoComplete="off" value={x.form.secret_key} placeholder="sk_live_…" onChange={(e) => set({ secret_key: e.target.value })} />
          </Field>
          <Field label="Public key (optional)" hint="For the hosted checkout. Starts with pk_.">
            <Input value={x.form.public_key} autoComplete="off" placeholder="pk_live_…" onChange={(e) => set({ public_key: e.target.value })} />
          </Field>
          <label className="vst-check">
            <input type="checkbox" checked={x.form.auto_register_webhook} onChange={(e) => set({ auto_register_webhook: e.target.checked })} />
            Register Vula’s webhook with Yoco for me (recommended)
          </label>
          <div className="vst-row">
            <Button type="submit" Icon={Link2} disabled={x.loading || !x.form.secret_key}>{x.loading ? 'Connecting…' : connected ? 'Update keys' : 'Connect Yoco'}</Button>
            {x.showForm && connected && <Button type="button" variant="secondary" onClick={() => x.setShowForm(false)}>Cancel</Button>}
          </div>
        </form>
      ) : (
        <div className="vst-row">
          <Button variant="secondary" Icon={KeyRound} onClick={() => x.setShowForm(true)}>Update keys</Button>
          <Button variant="secondary" Icon={CheckCircle2} onClick={x.handleTest} disabled={x.testing}>{x.testing ? 'Testing…' : 'Test connection'}</Button>
          <Button variant="quiet" Icon={Unplug} className="vcn-danger-btn" onClick={x.handleDisconnect} disabled={x.loading}>Disconnect</Button>
        </div>
      )}
    </ConnectCard>
  )
}
