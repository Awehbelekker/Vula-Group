/**
 * WhatsAppConnectV2.jsx — the redesigned WhatsApp number connection in Settings (2026-10-19, UX
 * redesign Phase F). Same flow as VulaWhatsAppConnect (connect/useWhatsAppConnect.js): the
 * connected number, verified name and webhook state, Disconnect; or Connect, which opens Meta's
 * guided signup.
 */
import { Link2, Smartphone, Unplug } from 'lucide-react'
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
    ...(a.coexistence ? [['Shared with', 'Your WhatsApp Business app — reply from your phone any time; Vula steps back in that chat', 'ok']] : []),
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
            Keep the WhatsApp number your customers already know. You carry on using the WhatsApp Business app on your phone, and Vula answers on the same number. A secure Meta window guides you; there’s nothing to set up in Meta yourself.
          </p>
          <ul className="vcn-steps" aria-label="What you need">
            <li>The <b>WhatsApp Business</b> app on your phone, updated. On personal WhatsApp? Switch to WhatsApp Business first; your chats come with you.</li>
            <li>Your phone nearby: WhatsApp Business asks you to approve the link, and whether to share your chats and contacts with Vula.</li>
            <li>When you reply from your phone, Vula goes quiet in that chat. It picks the chat up again after 2 hours of silence.</li>
          </ul>
          {!FB_APP_ID && <p className="vcn-warn-box">The Vula Facebook app isn’t set up on this dashboard yet (VITE_FB_APP_ID), so connecting is switched off.</p>}
          {FB_APP_ID && !FB_CONFIG_ID && <p className="vcn-warn-box">WhatsApp signup isn’t set up on this dashboard yet (VITE_FB_CONFIG_ID), so connecting is switched off — ask Vula to link the number for you.</p>}
          <div className="vst-row">
            <Button Icon={Smartphone} onClick={() => x.handleConnect('app')} disabled={x.loading || !canConnect}>{x.loading ? 'Connecting…' : 'Use my WhatsApp Business number'}</Button>
            <Button variant="secondary" Icon={Link2} onClick={() => x.handleConnect('new')} disabled={x.loading || !canConnect}>Connect a new number</Button>
          </div>
          <p className="vst-sub">Takes about 2 minutes. A new number must not be on WhatsApp yet; Meta sends it a code by SMS or call.</p>
        </>
      )}
    </ConnectCard>
  )
}
