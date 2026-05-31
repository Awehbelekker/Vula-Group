/**
 * VulaYocoConnect.jsx
 *
 * "Connect Yoco" form for the tenant management drawer in Vula Admin.
 * Client pastes their Yoco API keys (from portal.yoco.com → Developers).
 * Backend validates against Yoco and optionally registers webhook automatically.
 */

import { useState, useEffect } from 'react'

const VULA_API = import.meta.env.VITE_API_URL ?? '/api'

export default function VulaYocoConnect({ tenantId, tenantName, adminEmail }) {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)

  const [form, setForm] = useState({
    secret_key: '',
    public_key: '',
    auto_register_webhook: true,
  })

  useEffect(() => {
    if (!tenantId) return
    fetch(`${VULA_API}/v1/yoco/status/${tenantId}`)
      .then(r => r.json())
      .then(data => {
        setStatus(data.status)
        setAccount(data)
      })
      .catch(() => setStatus('error'))
  }, [tenantId])

  async function handleConnect(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${VULA_API}/v1/yoco/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: tenantId,
          secret_key: form.secret_key.trim(),
          public_key: form.public_key.trim() || null,
          auto_register_webhook: form.auto_register_webhook,
          connected_by: adminEmail,
        }),
      })
      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || 'Connection failed')
      }
      const data = await resp.json()
      setAccount({
        status: data.status,
        mode: data.mode,
        webhook_registered: data.webhook_registered,
        masked_secret: data.masked_secret,
        public_key: form.public_key,
        last_test_at: new Date().toISOString(),
      })
      setStatus('connected')
      setShowForm(false)
      setForm({ secret_key: '', public_key: '', auto_register_webhook: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    try {
      const resp = await fetch(`${VULA_API}/v1/yoco/test/${tenantId}`, { method: 'POST' })
      const data = await resp.json()
      if (!data.ok) setError(`Test failed: HTTP ${data.status_code}`)
      else { setError(null); alert('✓ Yoco keys valid') }
    } catch (err) {
      setError(err.message)
    } finally {
      setTesting(false)
    }
  }

  async function handleDisconnect() {
    if (!confirm(`Disconnect Yoco for ${tenantName}? Pending checkouts will fail.`)) return
    setLoading(true)
    await fetch(`${VULA_API}/v1/yoco/disconnect/${tenantId}`, { method: 'DELETE' })
    setStatus('not_connected')
    setAccount(null)
    setLoading(false)
  }

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.icon}>💳</div>
        <div>
          <h3 style={styles.title}>Yoco Payments</h3>
          <p style={styles.subtitle}>South African card + EFT payments</p>
        </div>
        <StatusBadge status={status} mode={account?.mode} />
      </div>

      {status === 'connected' && account && !showForm && (
        <div style={styles.connectedInfo}>
          <div style={styles.infoRow}>
            <span style={styles.label}>Mode</span>
            <span style={{ ...styles.value, color: account.mode === 'live' ? '#22c55e' : '#f59e0b' }}>
              {account.mode === 'live' ? '🟢 LIVE' : '🟡 TEST'}
            </span>
          </div>
          <div style={styles.infoRow}>
            <span style={styles.label}>Secret key</span>
            <span style={{ ...styles.value, fontFamily: 'monospace' }}>{account.masked_secret}</span>
          </div>
          <div style={styles.infoRow}>
            <span style={styles.label}>Webhook</span>
            <span style={{ ...styles.value, color: account.webhook_registered ? '#22c55e' : '#f59e0b' }}>
              {account.webhook_registered ? '✓ Registered' : '⚠ Manual setup needed'}
            </span>
          </div>
          {account.last_test_at && (
            <div style={styles.infoRow}>
              <span style={styles.label}>Last verified</span>
              <span style={styles.value}>{new Date(account.last_test_at).toLocaleString('en-ZA')}</span>
            </div>
          )}
        </div>
      )}

      {error && <div style={styles.errorBox}>{error}</div>}

      {showForm || status !== 'connected' ? (
        <form onSubmit={handleConnect} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p style={styles.description}>
            Get keys from <a href="https://portal.yoco.com/dashboard/api-keys" target="_blank" rel="noopener noreferrer" style={styles.link}>portal.yoco.com → Developers → API Keys</a>
          </p>

          <div>
            <label style={styles.formLabel}>Secret key (sk_live_… or sk_test_…)</label>
            <input
              type="password"
              required
              value={form.secret_key}
              onChange={(e) => setForm(f => ({ ...f, secret_key: e.target.value }))}
              placeholder="sk_live_xxxxxxxxxxxxxxx"
              style={styles.input}
            />
          </div>

          <div>
            <label style={styles.formLabel}>Public key (optional, for hosted checkout)</label>
            <input
              type="text"
              value={form.public_key}
              onChange={(e) => setForm(f => ({ ...f, public_key: e.target.value }))}
              placeholder="pk_live_xxxxxxxxxxxxxxx"
              style={styles.input}
            />
          </div>

          <label style={styles.checkboxLabel}>
            <input
              type="checkbox"
              checked={form.auto_register_webhook}
              onChange={(e) => setForm(f => ({ ...f, auto_register_webhook: e.target.checked }))}
            />
            <span>Auto-register Vula webhook with Yoco (recommended)</span>
          </label>

          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" disabled={loading || !form.secret_key} style={loading ? styles.btnDisabled : styles.btn}>
              {loading ? 'Connecting…' : status === 'connected' ? 'Update keys' : 'Connect Yoco'}
            </button>
            {showForm && status === 'connected' && (
              <button type="button" onClick={() => setShowForm(false)} style={styles.btnGhost}>Cancel</button>
            )}
          </div>
        </form>
      ) : (
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowForm(true)} style={styles.btnGhost}>Update keys</button>
          <button onClick={handleTest} disabled={testing} style={styles.btnGhost}>
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          <button onClick={handleDisconnect} disabled={loading} style={styles.btnDanger}>
            Disconnect
          </button>
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status, mode }) {
  const configs = {
    connected: {
      label: mode === 'test' ? 'Test mode' : 'Live',
      color: mode === 'test' ? '#f59e0b' : '#22c55e',
      bg: mode === 'test' ? 'rgba(245,158,11,0.15)' : 'rgba(34,197,94,0.15)',
    },
    error: { label: 'Error', color: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
    not_connected: { label: 'Not connected', color: '#6b7280', bg: 'rgba(107,114,128,0.15)' },
  }
  const c = configs[status] || configs.not_connected
  return <span style={{ ...styles.badge, color: c.color, background: c.bg }}>{c.label}</span>
}

const styles = {
  card: {
    background: '#111111',
    border: '1px solid #2a2a2a',
    borderRadius: 8,
    padding: 24,
    maxWidth: 480,
    marginTop: 16,
  },
  header: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 },
  icon: { fontSize: 32 },
  title: { margin: 0, color: '#f5f2ec', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: '#6b7280', fontSize: 13 },
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600 },
  connectedInfo: {
    background: '#0a0a0a', border: '1px solid #1a1a1a', borderRadius: 6,
    padding: 16, marginBottom: 16,
  },
  infoRow: {
    display: 'flex', justifyContent: 'space-between',
    padding: '6px 0', borderBottom: '1px solid #1a1a1a',
  },
  label: { color: '#6b7280', fontSize: 13 },
  value: { color: '#f5f2ec', fontSize: 13, fontWeight: 500 },
  description: { color: '#9ca3af', fontSize: 13, lineHeight: 1.6, margin: 0 },
  link: { color: '#C4861A', textDecoration: 'underline' },
  formLabel: {
    display: 'block', color: '#9ca3af', fontSize: 12, marginBottom: 4, fontWeight: 500,
  },
  input: {
    width: '100%', padding: '10px 12px', borderRadius: 6,
    background: '#0a0a0a', border: '1px solid #2a2a2a',
    color: '#f5f2ec', fontSize: 13, fontFamily: 'monospace',
    boxSizing: 'border-box',
  },
  checkboxLabel: {
    display: 'flex', alignItems: 'center', gap: 8,
    color: '#9ca3af', fontSize: 13, cursor: 'pointer',
  },
  errorBox: {
    background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
    color: '#ef4444', borderRadius: 6, padding: '10px 14px',
    fontSize: 13, marginBottom: 12,
  },
  btn: {
    background: '#22c55e', color: '#fff', border: 'none', borderRadius: 6,
    padding: '10px 20px', fontSize: 14, fontWeight: 600, cursor: 'pointer', flex: 1,
  },
  btnDisabled: {
    background: '#2a2a2a', color: '#6b7280', border: 'none', borderRadius: 6,
    padding: '10px 20px', fontSize: 14, cursor: 'not-allowed', flex: 1,
  },
  btnGhost: {
    background: 'transparent', color: '#9ca3af',
    border: '1px solid #2a2a2a', borderRadius: 6,
    padding: '8px 16px', fontSize: 13, cursor: 'pointer',
  },
  btnDanger: {
    background: 'transparent', color: '#ef4444',
    border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6,
    padding: '8px 16px', fontSize: 13, cursor: 'pointer',
  },
}
