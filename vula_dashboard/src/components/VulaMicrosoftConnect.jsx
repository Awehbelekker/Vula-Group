/**
 * VulaMicrosoftConnect.jsx — one-click "Connect Microsoft" (OneDrive + Outlook), draft-only mail.
 * Mirrors VulaGoogleConnect: popup OAuth → backend callback → status poll.
 */
import { useState, useEffect, useCallback, useRef } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

export default function VulaMicrosoftConnect({ tenantId, tenantName }) {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/microsoft/status/${tenantId}`)
      const d = await r.json()
      setStatus(d.status); setAccount(d)
    } catch { setStatus('error') }
  }, [tenantId])

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => {
    const onMsg = (e) => { if (e.data === 'microsoft-connected') loadStatus() }
    const onFocus = () => loadStatus()
    window.addEventListener('message', onMsg); window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('message', onMsg); window.removeEventListener('focus', onFocus); if (pollRef.current) clearInterval(pollRef.current) }
  }, [loadStatus])

  const handleConnect = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/microsoft/authorize-url?tenant_id=${encodeURIComponent(tenantId)}`)
      const d = await r.json()
      if (!d.url) throw new Error(d.error || 'Microsoft app not configured.')
      window.open(d.url, 'ms-oauth', 'width=520,height=680')
      let ticks = 0
      pollRef.current = setInterval(() => { loadStatus(); if (++ticks > 30 || status === 'connected') clearInterval(pollRef.current) }, 3000)
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }, [tenantId, loadStatus, status])

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.icon}>🟦</div>
        <div>
          <h3 style={styles.title}>Microsoft (OneDrive &amp; Outlook)</h3>
          <p style={styles.subtitle}>Find &amp; file OneDrive docs · draft emails</p>
        </div>
        <StatusBadge status={status} />
      </div>

      {status === 'connected' && account && (
        <div style={styles.connectedInfo}>
          <div style={styles.infoRow}><span style={styles.label}>Account</span><span style={styles.value}>{account.email || '—'}</span></div>
          <div style={styles.infoRow}><span style={styles.label}>Email</span><span style={styles.value}>Draft-only (you approve &amp; send)</span></div>
        </div>
      )}
      {error && <div style={styles.errorBox}>{error}</div>}

      {status !== 'connected' ? (
        <div>
          <p style={styles.description}>
            Connect {tenantName || 'your'} Microsoft 365 so Vula can find &amp; file OneDrive documents
            and draft Outlook replies. Email is draft-only — nothing sends without you.
          </p>
          <button onClick={handleConnect} disabled={loading} style={loading ? styles.btnDisabled : styles.btn}>
            {loading ? 'Opening Microsoft…' : '🔗 Connect Microsoft'}
          </button>
          <p style={styles.hint}>A Microsoft window opens for you to approve. ~30 seconds.</p>
        </div>
      ) : (
        <button onClick={handleConnect} disabled={loading} style={styles.btnGhost}>{loading ? '…' : 'Reconnect'}</button>
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const c = { connected: { l: 'Connected', c: '#22c55e', b: 'rgba(34,197,94,0.15)' },
    error: { l: 'Error', c: '#ef4444', b: 'rgba(239,68,68,0.15)' },
    not_connected: { l: 'Not connected', c: '#6b7280', b: 'rgba(107,114,128,0.15)' } }[status] || { l: 'Not connected', c: '#6b7280', b: 'rgba(107,114,128,0.15)' }
  return <span style={{ ...styles.badge, color: c.c, background: c.b }}>{c.l}</span>
}

const styles = {
  card: { background: '#111111', border: '1px solid #2a2a2a', borderRadius: 8, padding: 24, maxWidth: 480 },
  header: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 },
  icon: { fontSize: 32 }, title: { margin: 0, color: '#f5f2ec', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: '#6b7280', fontSize: 13 },
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600 },
  connectedInfo: { background: '#0a0a0a', border: '1px solid #1a1a1a', borderRadius: 6, padding: 16, marginBottom: 16 },
  infoRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid #1a1a1a' },
  label: { color: '#6b7280', fontSize: 13 }, value: { color: '#f5f2ec', fontSize: 13, fontWeight: 500 },
  description: { color: '#9ca3af', fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' },
  errorBox: { background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 12 },
  btn: { background: '#0078D4', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: '#2a2a2a', color: '#6b7280', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnGhost: { background: 'transparent', color: '#9ca3af', border: '1px solid #2a2a2a', borderRadius: 6, padding: '8px 16px', fontSize: 13, cursor: 'pointer' },
  hint: { color: '#4b5563', fontSize: 12, marginTop: 10, textAlign: 'center' },
}
