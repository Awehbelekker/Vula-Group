/**
 * WhatsAppConnectV2.jsx — the redesigned WhatsApp number connection in Settings (2026-10-19, UX
 * redesign Phase F). Same flow as VulaWhatsAppConnect (connect/useWhatsAppConnect.js): the
 * connected number, verified name and webhook state, Disconnect; or Connect, which opens Meta's
 * guided signup.
 */
import { Link2, Unplug } from 'lucide-react'
import { Button } from '../../ui/index.jsx'
import { ConnectCard } from './ConnectCard.jsx'
import { FB_APP_ID, FB_CONFIG_ID, useWhatsAppConnect } from './useWhatsAppConnect'

export default function WhatsAppConnectV2({ tenantId, tenantName, adminEmail }) {
  const x = useWhatsAppConnect({ tenantId, tenantName, adminEmail })
  const connected = x.status === 'connected'
  const a = x.account || {}
  const rows = connected && x.account ? [
    ['Number', a.phone_number],
    ['Business name', a.verified_name],
    ['Webhook', a.webhook_registered ? 'Registered' : 'Not registered', a.webhook_registered ? 'ok' : 'warn'],
    // Number health from Meta (GET /v1/whatsapp/health): quality, daily limit, name, registered.
    ...((x.health && x.health.rows) || []).map(r => [r.label, r.value, r.tone === 'danger' ? 'warn' : r.tone]),
    ...(x.health && x.health.error ? [['Number health', x.health.error, 'warn']] : []),
  ] : []
  const canConnect = Boolean(FB_APP_ID && FB_CONFIG_ID)
  return (
    <ConnectCard status={x.status} rows={rows} error={x.error}>
      {connected ? (
        <div className="vst-row">
          <Button variant="quiet" Icon={Unplug} className="vcn-danger-btn" onClick={x.handleDisconnect} disabled={x.loading}>{x.loading ? 'Disconnecting…' : 'Disconnect'}</Button>
        </div>
      ) : (
        <>
          <p className="vst-body">
            Connect {tenantName}&apos;s WhatsApp Business account. You’re guided through a secure Meta-hosted flow — no API keys or developer access required.
          </p>
          {!FB_APP_ID && <p className="vcn-warn-box">The Vula Facebook app isn’t set up on this dashboard yet (VITE_FB_APP_ID), so connecting is switched off.</p>}
          {FB_APP_ID && !FB_CONFIG_ID && <p className="vcn-warn-box">WhatsApp signup isn’t set up on this dashboard yet (VITE_FB_CONFIG_ID), so connecting is switched off — ask Vula to link the number for you.</p>}
          <div className="vst-row">
            <Button Icon={Link2} onClick={x.handleConnect} disabled={x.loading || !canConnect}>{x.loading ? 'Connecting…' : 'Connect WhatsApp'}</Button>
          </div>
          <p className="vst-sub">A Meta window guides you through the connection. Takes about 2 minutes.</p>
        </>
      )}
    </ConnectCard>
  )
}
