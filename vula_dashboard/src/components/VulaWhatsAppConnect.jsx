/**
 * VulaWhatsAppConnect.jsx
 *
 * "Connect WhatsApp" button using Meta Embedded Signup.
 * Shown in Vula Admin for each commerce tenant.
 *
 * What happens when client clicks Connect:
 *   1. Meta popup opens (guided by Meta)
 *   2. Client logs into their Facebook account
 *   3. Client selects/creates their WhatsApp Business Account
 *   4. Client verifies their phone number
 *   5. Meta returns a code to this component
 *   6. Component sends code to Vula backend
 *   7. Backend exchanges code → token → registers webhook → stores in Supabase
 *   8. Done. WhatsApp ordering is live for that client.
 *
 * Ian never needs to touch Meta credentials again.
 *
 * Flow: settings/connect/useWhatsAppConnect.js (shared with the redesign's WhatsAppConnectV2).
 */

import { useState } from 'react'
import { FB_APP_ID, FB_CONFIG_ID, PURPOSES, PURPOSE_HINTS, useWhatsAppConnect } from './settings/connect/useWhatsAppConnect'

export default function VulaWhatsAppConnect({ tenantId, tenantName, adminEmail }) {
  const x = useWhatsAppConnect({ tenantId, tenantName, adminEmail })
  const { status, account, loading, error, handleConnect } = x
  const [adding, setAdding] = useState(false)
  const [newPurpose, setNewPurpose] = useState('team')
  const off = loading || !FB_APP_ID || !FB_CONFIG_ID

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.waIcon}>💬</div>
        <div>
          <h3 style={styles.title}>WhatsApp</h3>
          <p style={styles.subtitle}>Ordering, confirmations &amp; broadcasts</p>
        </div>
        <StatusBadge status={status} />
      </div>

      {status === 'connected' && account && (
        <div style={styles.connectedInfo}>
          <div style={styles.infoRow}>
            <span style={styles.label}>Main number</span>
            <span style={styles.value}>{account.phone_number}</span>
          </div>
          <div style={styles.infoRow}>
            <span style={styles.label}>Business name</span>
            <span style={styles.value}>{account.verified_name}</span>
          </div>
          {account.coexistence && (
            <div style={styles.infoRow}>
              <span style={styles.label}>Shared with</span>
              <span style={styles.value}>Your WhatsApp Business app</span>
            </div>
          )}
          <div style={styles.infoRow}>
            <span style={styles.label}>Webhook</span>
            <span style={{ ...styles.value, color: account.webhook_registered ? 'var(--ok)' : 'var(--warn)' }}>
              {account.webhook_registered ? '✓ Registered' : '⚠ Not registered'}
            </span>
          </div>
        </div>
      )}

      {error && (
        <div style={styles.errorBox}>
          {error}
        </div>
      )}

      {status !== 'connected' ? (
        <div>
          <p style={styles.description}>
            Keep the WhatsApp number your customers already know: carry on using the WhatsApp Business
            app on your phone, and Vula answers on the same number. You need the WhatsApp Business app
            (updated) and your phone nearby to approve the link. When you reply from your phone, Vula goes
            quiet in that chat for 2 hours.
          </p>

          {!FB_APP_ID && (
            <div style={styles.warningBox}>
              ⚠ VITE_FB_APP_ID not set. Create the Vula Facebook App first.
            </div>
          )}
          {FB_APP_ID && !FB_CONFIG_ID && (
            <div style={styles.warningBox}>
              ⚠ WhatsApp signup isn&apos;t set up on this dashboard yet (VITE_FB_CONFIG_ID) — ask Vula to link the number for you.
            </div>
          )}

          <button
            onClick={() => handleConnect('app')}
            disabled={loading || !FB_APP_ID || !FB_CONFIG_ID}
            style={loading || !FB_APP_ID || !FB_CONFIG_ID ? styles.btnDisabled : styles.btn}
          >
            {loading ? 'Connecting…' : '📱 Use my WhatsApp Business number'}
          </button>
          <button
            onClick={() => handleConnect('new')}
            disabled={loading || !FB_APP_ID || !FB_CONFIG_ID}
            style={{ ...(loading || !FB_APP_ID || !FB_CONFIG_ID ? styles.btnDisabled : styles.btn), marginTop: 8, background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }}
          >
            🔗 Connect a new number
          </button>

          <p style={styles.hint}>
            The client will see a Meta popup guiding them through the connection.
            Takes about 2 minutes.
          </p>
        </div>
      ) : (
        <div>
          {/* Every number, what it's for, the main one (2026-10-25) */}
          <ul aria-label="Your WhatsApp numbers" style={styles.list}>
            {x.numbers.map(n => (
              <li key={n.phone_number_id || n.phone_number} style={styles.item}>
                <div style={styles.infoRow}>
                  <span style={styles.value}>{n.phone_number}</span>
                  {n.is_primary && <span style={{ ...styles.badge, color: 'var(--ok)', background: 'rgba(34,197,94,0.15)' }}>Main number</span>}
                </div>
                <label style={styles.label}>
                  What it’s for
                  <select aria-label={`What ${n.phone_number} is for`} value={n.purpose || 'shop'} style={styles.select}
                    disabled={x.busyNumber === n.phone_number_id || !n.phone_number_id}
                    onChange={e => x.updateNumber(n.phone_number_id, { purpose: e.target.value })}>
                    {PURPOSES.map(([k, label]) => (
                      <option key={k} value={k} disabled={k === 'team' && n.is_primary}>{label}</option>))}
                  </select>
                </label>
                <p style={styles.hint}>{PURPOSE_HINTS[n.purpose]}</p>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {!n.is_primary && n.purpose !== 'team' && n.phone_number_id && (
                    <button style={styles.btnGhost} disabled={x.busyNumber === n.phone_number_id}
                      onClick={() => x.updateNumber(n.phone_number_id, { is_primary: true })}
                      aria-label={`Make ${n.phone_number} the main number`}>Make main</button>)}
                  <button onClick={() => x.disconnectNumber(n)} disabled={loading || x.busyNumber === n.phone_number_id}
                    style={styles.btnDanger} aria-label={`Disconnect ${n.phone_number}`}>Disconnect</button>
                </div>
              </li>
            ))}
          </ul>
          <p style={styles.hint}>Replies go from the number the person wrote to; Vula’s own messages from your main number.</p>
          {!adding ? (
            <button style={{ ...styles.btnGhost, width: '100%' }} onClick={() => setAdding(true)}>➕ Add a number</button>
          ) : (
            <div style={styles.connectedInfo}>
              <label style={styles.label}>
                What the new number is for
                <select aria-label="What the new number is for" value={newPurpose} style={styles.select}
                  onChange={e => setNewPurpose(e.target.value)}>
                  {PURPOSES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
              </label>
              <p style={styles.hint}>{PURPOSE_HINTS[newPurpose]}</p>
              <button onClick={() => handleConnect('app', newPurpose)} disabled={off} style={off ? styles.btnDisabled : styles.btn}>
                {loading ? 'Connecting…' : '📱 Use my WhatsApp Business number'}
              </button>
              <button onClick={() => handleConnect('new', newPurpose)} disabled={off}
                style={{ ...(off ? styles.btnDisabled : styles.btn), marginTop: 8, background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }}>
                🔗 Connect a new number
              </button>
              <button style={{ ...styles.btnGhost, marginTop: 8 }} onClick={() => setAdding(false)}>Cancel</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const configs = {
    connected: { label: 'Connected', color: 'var(--ok)', bg: 'rgba(34,197,94,0.15)' },
    pending: { label: 'Pending', color: 'var(--warn)', bg: 'rgba(245,158,11,0.15)' },
    error: { label: 'Error', color: 'var(--danger)', bg: 'rgba(239,68,68,0.15)' },
    not_connected: { label: 'Not connected', color: 'var(--muted)', bg: 'rgba(107,114,128,0.15)' },
  }
  const c = configs[status] || configs.not_connected
  return (
    <span style={{ ...styles.badge, color: c.color, background: c.bg }}>
      {c.label}
    </span>
  )
}

const styles = {
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    padding: 24,
    maxWidth: 480,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 20,
  },
  waIcon: { fontSize: 32 },
  title: { margin: 0, color: 'var(--ink)', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: 'var(--muted)', fontSize: 13 },
  badge: {
    marginLeft: 'auto',
    padding: '4px 10px',
    borderRadius: 20,
    fontSize: 12,
    fontWeight: 600,
  },
  connectedInfo: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    padding: 16,
    marginBottom: 16,
  },
  infoRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '6px 0',
    borderBottom: '1px solid var(--border-soft)',
  },
  label: { color: 'var(--muted)', fontSize: 13 },
  value: { color: 'var(--ink)', fontSize: 13, fontWeight: 500 },
  description: { color: 'var(--muted)', fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' },
  errorBox: {
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    color: 'var(--danger)',
    borderRadius: 6,
    padding: '10px 14px',
    fontSize: 13,
    marginBottom: 12,
  },
  warningBox: {
    background: 'rgba(245,158,11,0.1)',
    border: '1px solid rgba(245,158,11,0.3)',
    color: 'var(--warn)',
    borderRadius: 6,
    padding: '10px 14px',
    fontSize: 13,
    marginBottom: 12,
  },
  btn: {
    background: '#25D366',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    padding: '12px 24px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    width: '100%',
  },
  btnDisabled: {
    background: 'var(--surface-alt)',
    color: 'var(--muted)',
    border: 'none',
    borderRadius: 6,
    padding: '12px 24px',
    fontSize: 14,
    cursor: 'not-allowed',
    width: '100%',
  },
  btnDanger: {
    background: 'transparent',
    color: 'var(--danger)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 6,
    padding: '8px 16px',
    fontSize: 13,
    cursor: 'pointer',
  },
  hint: { color: 'var(--muted)', fontSize: 12, marginTop: 10, textAlign: 'center' },
  list: { listStyle: 'none', margin: '0 0 8px', padding: 0, display: 'flex', flexDirection: 'column', gap: 12 },
  item: { border: '1px solid var(--border)', borderRadius: 6, padding: 12, display: 'flex', flexDirection: 'column', gap: 6 },
  select: { display: 'block', width: '100%', marginTop: 4, minHeight: 40, padding: '8px 10px', borderRadius: 6,
    border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--ink)', fontSize: 14 },
  btnGhost: {
    background: 'transparent', color: 'var(--ink)', border: '1px solid var(--border)', borderRadius: 6,
    padding: '8px 16px', fontSize: 13, cursor: 'pointer', minHeight: 40,
  },
}
