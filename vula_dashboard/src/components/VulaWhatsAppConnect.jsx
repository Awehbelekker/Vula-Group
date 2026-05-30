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
 */

import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
const FB_APP_ID = import.meta.env.VITE_FB_APP_ID || ''
const FB_CONFIG_ID = import.meta.env.VITE_FB_CONFIG_ID || ''

export default function VulaWhatsAppConnect({ tenantId, tenantName, adminEmail }) {
  const [status, setStatus] = useState(null)   // null | 'connected' | 'pending' | 'error'
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Load current connection status
  useEffect(() => {
    if (!tenantId) return
    fetch(`${VULA_API}/v1/whatsapp/connect/status/${tenantId}`)
      .then(r => r.json())
      .then(data => {
        setStatus(data.status)
        setAccount(data)
      })
      .catch(() => setStatus('error'))
  }, [tenantId])

  // Load Meta Facebook SDK
  useEffect(() => {
    if (!FB_APP_ID || window.FB) return
    const script = document.createElement('script')
    script.src = 'https://connect.facebook.net/en_US/sdk.js'
    script.async = true
    script.defer = true
    script.crossOrigin = 'anonymous'
    document.head.appendChild(script)
    window.fbAsyncInit = () => {
      window.FB.init({
        appId: FB_APP_ID,
        autoLogAppEvents: true,
        xfbml: true,
        version: 'v19.0',
      })
    }
  }, [])

  const handleConnect = useCallback(() => {
    if (!window.FB) {
      setError('Facebook SDK not loaded. Please refresh and try again.')
      return
    }

    setLoading(true)
    setError(null)

    window.FB.login(
      async (response) => {
        if (!response.authResponse) {
          setLoading(false)
          setError('Connection cancelled.')
          return
        }

        try {
          const code = response.authResponse.code

          const result = await fetch(`${VULA_API}/v1/whatsapp/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              tenant_id: tenantId,
              code,
              connected_by: adminEmail,
            }),
          })

          if (!result.ok) {
            const err = await result.json()
            throw new Error(err.detail || 'Connection failed')
          }

          const data = await result.json()
          setAccount(data)
          setStatus('connected')
        } catch (err) {
          setError(err.message)
          setStatus('error')
        } finally {
          setLoading(false)
        }
      },
      {
        config_id: FB_CONFIG_ID,
        response_type: 'code',
        override_default_response_type: true,
        extras: {
          setup: {},
          featureType: '',
          sessionInfoVersion: '2',
        },
      }
    )
  }, [tenantId, adminEmail])

  const handleDisconnect = async () => {
    if (!confirm(`Disconnect WhatsApp for ${tenantName}? Ordering will stop immediately.`)) return
    setLoading(true)
    await fetch(`${VULA_API}/v1/whatsapp/disconnect/${tenantId}`, { method: 'DELETE' })
    setStatus('not_connected')
    setAccount(null)
    setLoading(false)
  }

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
          <div style={styles.infoRow}>
            <span style={styles.label}>Webhook</span>
            <span style={{ ...styles.value, color: account.webhook_registered ? '#22c55e' : '#f59e0b' }}>
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
            Connect {tenantName}&apos;s WhatsApp Business account. The client will be guided through
            a secure Meta-hosted flow — no API keys or developer access required.
          </p>

          {!FB_APP_ID && (
            <div style={styles.warningBox}>
              ⚠ VITE_FB_APP_ID not set. Create the Vula Facebook App first.
            </div>
          )}

          <button
            onClick={handleConnect}
            disabled={loading || !FB_APP_ID}
            style={loading || !FB_APP_ID ? styles.btnDisabled : styles.btn}
          >
            {loading ? 'Connecting…' : '🔗 Connect WhatsApp'}
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
    connected: { label: 'Connected', color: '#22c55e', bg: 'rgba(34,197,94,0.15)' },
    pending: { label: 'Pending', color: '#f59e0b', bg: 'rgba(245,158,11,0.15)' },
    error: { label: 'Error', color: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
    not_connected: { label: 'Not connected', color: '#6b7280', bg: 'rgba(107,114,128,0.15)' },
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
    background: '#111111',
    border: '1px solid #2a2a2a',
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
  title: { margin: 0, color: '#f5f2ec', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: '#6b7280', fontSize: 13 },
  badge: {
    marginLeft: 'auto',
    padding: '4px 10px',
    borderRadius: 20,
    fontSize: 12,
    fontWeight: 600,
  },
  connectedInfo: {
    background: '#0a0a0a',
    border: '1px solid #1a1a1a',
    borderRadius: 6,
    padding: 16,
    marginBottom: 16,
  },
  infoRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '6px 0',
    borderBottom: '1px solid #1a1a1a',
  },
  label: { color: '#6b7280', fontSize: 13 },
  value: { color: '#f5f2ec', fontSize: 13, fontWeight: 500 },
  description: { color: '#9ca3af', fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' },
  errorBox: {
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    color: '#ef4444',
    borderRadius: 6,
    padding: '10px 14px',
    fontSize: 13,
    marginBottom: 12,
  },
  warningBox: {
    background: 'rgba(245,158,11,0.1)',
    border: '1px solid rgba(245,158,11,0.3)',
    color: '#f59e0b',
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
    background: '#2a2a2a',
    color: '#6b7280',
    border: 'none',
    borderRadius: 6,
    padding: '12px 24px',
    fontSize: 14,
    cursor: 'not-allowed',
    width: '100%',
  },
  btnDanger: {
    background: 'transparent',
    color: '#ef4444',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 6,
    padding: '8px 16px',
    fontSize: 13,
    cursor: 'pointer',
  },
  hint: { color: '#4b5563', fontSize: 12, marginTop: 10, textAlign: 'center' },
}
