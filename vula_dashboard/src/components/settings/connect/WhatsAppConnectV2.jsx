/**
 * WhatsAppConnectV2.jsx — the redesigned WhatsApp number connection in Settings (2026-10-19, UX
 * redesign Phase F). Same flow as VulaWhatsAppConnect (connect/useWhatsAppConnect.js): the
 * connected number, verified name and webhook state, Disconnect; or Connect, which opens Meta's
 * guided signup.
 *
 * 2026-10-25: a business can have several numbers. Each is listed with what it's for (shop,
 * bookings, assistant, team only), which is the main one, and Disconnect; "Add a number" asks
 * what the new one is for, then connects it the same two ways. A number on Vula's own WhatsApp
 * account can be moved to the owner's own Meta account ("Move to my Meta account").
 */
import { useState } from 'react'
import { ArrowRightLeft, Link2, Plus, Smartphone, Star, Unplug } from 'lucide-react'
import { Button, Field, Select, StatusChip } from '../../ui/index.jsx'
import { ConnectCard } from './ConnectCard.jsx'
import { FB_APP_ID, FB_CONFIG_ID, PURPOSES, PURPOSE_HINTS, useWhatsAppConnect } from './useWhatsAppConnect'

function ConnectChoices({ x, canConnect, purpose }) {
  return (
    <>
      <ul className="vcn-steps" aria-label="What you need">
        <li>To keep a number you already use: the <b>WhatsApp Business</b> app on your phone, updated. On personal WhatsApp? Switch to WhatsApp Business first; your chats come with you.</li>
        <li>Your phone nearby: WhatsApp Business asks you to approve the link, and whether to share your chats and contacts with Vula.</li>
        <li>When you reply from your phone, Vula goes quiet in that chat. It picks the chat up again after 2 hours of silence.</li>
      </ul>
      {!FB_APP_ID && <p className="vcn-warn-box">The Vula Facebook app isn’t set up on this dashboard yet (VITE_FB_APP_ID), so connecting is switched off.</p>}
      {FB_APP_ID && !FB_CONFIG_ID && <p className="vcn-warn-box">WhatsApp signup isn’t set up on this dashboard yet (VITE_FB_CONFIG_ID), so connecting is switched off — ask Vula to link the number for you.</p>}
      <div className="vst-row">
        <Button Icon={Smartphone} onClick={() => x.handleConnect('app', purpose)} disabled={x.loading || !canConnect}>{x.loading ? 'Connecting…' : 'Use my WhatsApp Business number'}</Button>
        <Button variant="secondary" Icon={Link2} onClick={() => x.handleConnect('new', purpose)} disabled={x.loading || !canConnect}>Connect a new number</Button>
      </div>
      <p className="vst-sub">Takes about 2 minutes. A new number must not be on WhatsApp yet; Meta sends it a code by SMS or call.</p>
    </>
  )
}

// Moving a number from Vula's WhatsApp account to the owner's own: Meta moves it when the owner
// enters it in its signup window; it keeps working throughout, with its name, quality and limits.
function MoveSteps({ n, x, canConnect, onCancel }) {
  return (
    <div className="vst-card vcn-token" style={{ flexBasis: '100%' }}>
      <p className="vst-body">{n.phone_number} is on Vula’s WhatsApp account. Moving it to your own Meta account makes it yours; your customers notice nothing.</p>
      <ol className="vcn-steps" aria-label={`Before you move ${n.phone_number}`}>
        <li>Your business is <b>verified</b> in Meta (business.facebook.com › Security Centre), with a payment method for WhatsApp.</li>
        <li>Ask Vula to switch off the number’s two-step PIN first; it takes a minute.</li>
        <li>Whoever has this number’s SIM is nearby: Meta sends it a code by SMS or call.</li>
        <li>In the Meta window, choose your business and enter <b>{n.phone_number}</b>.</li>
      </ol>
      <p className="vst-sub">The number keeps working the whole time and keeps its name, quality rating and daily limit. Most of your approved message templates come along; Meta re-checks them.</p>
      <div className="vst-row">
        <Button Icon={ArrowRightLeft} disabled={x.loading || !canConnect}
          onClick={() => x.handleConnect('new', null, { move_phone_number_id: n.phone_number_id })}>{x.loading ? 'Moving…' : 'Start the move'}</Button>
        <Button variant="quiet" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

export default function WhatsAppConnectV2({ tenantId, tenantName, adminEmail }) {
  const x = useWhatsAppConnect({ tenantId, tenantName, adminEmail })
  const [adding, setAdding] = useState(false)
  const [newPurpose, setNewPurpose] = useState('team')
  const [moving, setMoving] = useState(null)        // the number whose move is being explained
  const connected = x.status === 'connected'
  const a = x.account || {}
  const rows = connected && x.account ? [
    ['Main number', a.phone_number],
    ['Business name', a.verified_name],
    ...(a.coexistence ? [['Shared with', 'Your WhatsApp Business app — reply from your phone any time; Vula steps back in that chat', 'ok']] : []),
    ['Webhook', a.webhook_registered ? 'Registered' : 'Not registered', a.webhook_registered ? 'ok' : 'warn'],
    // Number health from Meta (GET /v1/whatsapp/health): quality, daily limit, name, registered.
    ...((x.health && x.health.rows) || []).map(r => [r.label, r.value, r.tone === 'danger' ? 'warn' : r.tone]),
    ...(x.health && x.health.error ? [['Number health', x.health.error, 'warn']] : []),
  ] : []
  const canConnect = Boolean(FB_APP_ID && FB_CONFIG_ID)
  return (
    <ConnectCard status={x.status} rows={rows} error={x.error} notice={x.notice}>
      {connected ? (
        <>
          <ul className="vcn-list" aria-label="Your WhatsApp numbers">
            {x.numbers.map(n => {
              const busy = x.busyNumber === n.phone_number_id
              return (
                <li key={n.phone_number_id || n.phone_number} className="vcn-item">
                  <div className="vcn-item-main">
                    <span className="vst-row"><span className="vst-strong">{n.phone_number}</span>{n.is_primary && <StatusChip tone="highlight">Main number</StatusChip>}</span>
                    <span className="vst-sub">
                      {[n.label || n.verified_name, n.coexistence && 'shared with your WhatsApp Business app',
                        n.on_vula_account && "on Vula's WhatsApp account"].filter(Boolean).join(' · ')}
                    </span>
                    <Field label="What it’s for" hint={PURPOSE_HINTS[n.purpose]} style={{ marginTop: 8 }}>
                      <Select aria-label={`What ${n.phone_number} is for`} value={n.purpose || 'shop'} disabled={busy || !n.phone_number_id}
                        onChange={e => x.updateNumber(n.phone_number_id, { purpose: e.target.value })}>
                        {PURPOSES.map(([k, label]) => (
                          <option key={k} value={k} disabled={k === 'team' && n.is_primary}>{label}{k === 'team' && n.is_primary ? ' (not for the main number)' : ''}</option>))}
                      </Select>
                    </Field>
                  </div>
                  <div className="vst-row">
                    {!n.is_primary && n.purpose !== 'team' && n.phone_number_id && (
                      <Button size="sm" variant="secondary" Icon={Star} disabled={busy}
                        onClick={() => x.updateNumber(n.phone_number_id, { is_primary: true })}
                        aria-label={`Make ${n.phone_number} the main number`}>Make main</Button>)}
                    {n.on_vula_account && n.phone_number_id && (
                      <Button size="sm" variant="secondary" Icon={ArrowRightLeft} disabled={busy || x.loading}
                        onClick={() => setMoving(moving === n.phone_number_id ? null : n.phone_number_id)}
                        aria-expanded={moving === n.phone_number_id}
                        aria-label={`Move ${n.phone_number} to my own Meta account`}>Move to my Meta account</Button>)}
                    <Button size="sm" variant="quiet" Icon={Unplug} className="vcn-danger-btn" disabled={busy || x.loading}
                      onClick={() => x.disconnectNumber(n)} aria-label={`Disconnect ${n.phone_number}`}>{busy ? 'Working…' : 'Disconnect'}</Button>
                  </div>
                  {moving === n.phone_number_id && <MoveSteps n={n} x={x} canConnect={canConnect} onCancel={() => setMoving(null)} />}
                </li>)
            })}
          </ul>
          <p className="vst-sub">Replies always go from the number the person wrote to. Messages Vula starts — order updates, reminders, login codes — go from your main number.</p>
          {!adding ? (
            <div className="vst-row">
              <Button variant="secondary" Icon={Plus} onClick={() => setAdding(true)}>Add a number</Button>
            </div>
          ) : (
            <div className="vst-card vcn-token">
              <Field label="What the new number is for" hint={PURPOSE_HINTS[newPurpose]}>
                <Select aria-label="What the new number is for" value={newPurpose} onChange={e => setNewPurpose(e.target.value)}>
                  {PURPOSES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </Select>
              </Field>
              <ConnectChoices x={x} canConnect={canConnect} purpose={newPurpose} />
              <div className="vst-row"><Button variant="quiet" onClick={() => setAdding(false)}>Cancel</Button></div>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="vst-body">
            Keep the WhatsApp number your customers already know. You carry on using the WhatsApp Business app on your phone, and Vula answers on the same number. A secure Meta window guides you; there’s nothing to set up in Meta yourself.
          </p>
          <ConnectChoices x={x} canConnect={canConnect} purpose={null} />
        </>
      )}
    </ConnectCard>
  )
}
