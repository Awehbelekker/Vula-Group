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

import { FB_APP_ID, FB_CONFIG_ID, useWhatsAppConnect } from './settings/connect/useWhatsAppConnect'

export default function VulaWhatsAppConnect({ tenantId, tenantName, adminEmail }) {
  const { status, account, loading, error, handleConnect, handleDisconnect } = useWhatsAppConnect({ tenantId, tenantName, adminEmail })

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
            <span style={styles.label}>Number</span>
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
        <button
          onClick={handleDisconnect}
          disabled={loading}
          style={styles.btnDanger}
        >
          {loading ? 'Disconnecting…' : 'Disconnect'}
        </button>
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
}
