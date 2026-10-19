/**
 * OAuthConnectV2.jsx — the redesigned Google and Microsoft connections in Settings (2026-10-19,
 * UX redesign Phase F). Same flow as VulaGoogleConnect / VulaMicrosoftConnect
 * (connect/useOAuthConnect.js): status, the connected account, sync health (Microsoft), and
 * Connect / Reconnect, which opens the provider's approval window.
 */
import { Link2, RefreshCw } from 'lucide-react'
import { Button } from '../../ui/index.jsx'
import { ConnectCard } from './ConnectCard.jsx'
import { timeAgo, useOAuthConnect } from './useOAuthConnect'

const PROVIDERS = {
  google: { label: 'Google', popupName: 'google-oauth', what: 'Google account', reach: 'find & file Drive documents and draft Gmail replies' },
  microsoft: { label: 'Microsoft', popupName: 'ms-oauth', what: 'Microsoft 365', reach: 'find & file OneDrive documents and draft Outlook replies' },
}

export default function OAuthConnectV2({ provider, tenantId, tenantName }) {
  const P = PROVIDERS[provider]
  const x = useOAuthConnect({ tenantId, provider, popupName: P.popupName, label: P.label })
  const connected = x.status === 'connected'
  const a = x.account || {}
  // Sync health is separate from the sign-in (migration 169): shown once a sync has run.
  const sync = connected && a.last_synced_at && (a.last_sync_status === 'error'
    ? ['Sync', `Failing — ${timeAgo(a.last_synced_at)}${a.last_sync_error ? ` · ${String(a.last_sync_error).slice(0, 120)}` : ''}`, 'danger']
    : ['Sync', `Last synced ${timeAgo(a.last_synced_at)}`])
  return (
    <ConnectCard status={x.status} error={x.error}
      rows={connected ? [['Account', a.email || '—'], ['Email', 'Draft-only (you approve & send)'], sync] : []}>
      {connected ? (
        <div className="vst-row">
          <Button variant="secondary" Icon={RefreshCw} onClick={x.handleConnect} disabled={x.loading}>{x.loading ? 'Opening…' : 'Reconnect'}</Button>
        </div>
      ) : (
        <>
          <p className="vst-body">
            Connect {tenantName || 'your'} {P.what} so Vula can {P.reach}. Email is draft-only — nothing sends without you.
          </p>
          <div className="vst-row">
            <Button Icon={Link2} onClick={x.handleConnect} disabled={x.loading}>{x.loading ? `Opening ${P.label}…` : `Connect ${P.label}`}</Button>
          </div>
          <p className="vst-sub">A {P.label} window opens for you to approve. About 30 seconds.</p>
        </>
      )}
    </ConnectCard>
  )
}
