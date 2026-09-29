/**
 * VulaGoogleConnect.jsx — one-click "Connect Google" (Drive + Gmail), draft-only email.
 * Mirrors VulaClickUpConnect: popup OAuth → backend callback → status poll.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { VULA_API } from '../lib/authFetch'


export default function VulaGoogleConnect({ tenantId, tenantName }) {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/google/status/${tenantId}`)
      const d = await r.json()
      setStatus(d.status); setAccount(d)
    } catch { setStatus('error') }
  }, [tenantId])

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => {
    const onMsg = (e) => { if (e.data === 'google-connected') loadStatus() }
    const onFocus = () => loadStatus()
    window.addEventListener('message', onMsg); window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('message', onMsg); window.removeEventListener('focus', onFocus); if (pollRef.current) clearInterval(pollRef.current) }
  }, [loadStatus])

  const handleConnect = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/google/authorize-url?tenant_id=${encodeURIComponent(tenantId)}`)
      const d = await r.json()
      if (!d.url) throw new Error(d.error || 'Google app not configured.')
      window.open(d.url, 'google-oauth', 'width=520,height=680')
      let ticks = 0
      pollRef.current = setInterval(() => { loadStatus(); if (++ticks > 30 || status === 'connected') clearInterval(pollRef.current) }, 3000)
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }, [tenantId, loadStatus, status])

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.icon}>🔵</div>
        <div>
          <h3 style={styles.title}>Google (Drive &amp; Gmail)</h3>
          <p style={styles.subtitle}>Find &amp; file Drive docs · draft emails</p>
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
            Connect {tenantName || 'your'} Google account so Vula can find &amp; file Drive documents
            and draft Gmail replies. Email is draft-only — nothing sends without you.
          </p>
          <button onClick={handleConnect} disabled={loading} style={loading ? styles.btnDisabled : styles.btn}>
            {loading ? 'Opening Google…' : '🔗 Connect Google'}
          </button>
          <p style={styles.hint}>A Google window opens for you to approve. ~30 seconds.</p>
        </div>
      ) : (
        <button onClick={handleConnect} disabled={loading} style={styles.btnGhost}>{loading ? '…' : 'Reconnect'}</button>
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const c = { connected: { l: 'Connected', c: 'var(--ok)', b: 'rgba(34,197,94,0.15)' },
    error: { l: 'Error', c: 'var(--danger)', b: 'rgba(239,68,68,0.15)' },
    not_connected: { l: 'Not connected', c: 'var(--muted)', b: 'rgba(107,114,128,0.15)' } }[status] || { l: 'Not connected', c: 'var(--muted)', b: 'rgba(107,114,128,0.15)' }
  return <span style={{ ...styles.badge, color: c.c, background: c.b }}>{c.l}</span>
}

const styles = {
  card: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 24, maxWidth: 480 },
  header: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 },
  icon: { fontSize: 32 }, title: { margin: 0, color: 'var(--ink)', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: 'var(--muted)', fontSize: 13 },
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600 },
  connectedInfo: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: 16, marginBottom: 16 },
  infoRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border-soft)' },
  label: { color: 'var(--muted)', fontSize: 13 }, value: { color: 'var(--ink)', fontSize: 13, fontWeight: 500 },
  description: { color: 'var(--muted)', fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' },
  errorBox: { background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--danger)', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 12 },
  btn: { background: '#4285F4', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: 'var(--surface-alt)', color: 'var(--muted)', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnGhost: { background: 'transparent', color: 'var(--faint)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 16px', fontSize: 13, cursor: 'pointer' },
  hint: { color: 'var(--muted)', fontSize: 12, marginTop: 10, textAlign: 'center' },
}
