/**
 * VulaClickUpConnect.jsx
 *
 * One-click "Connect ClickUp" (OAuth), mirroring VulaWhatsAppConnect.
 *
 * Flow:
 *   1. Click Connect → fetch the ClickUp consent URL from the backend
 *   2. Open it in a popup; the client approves in ClickUp
 *   3. ClickUp redirects to the Vula backend callback, which exchanges the code,
 *      auto-discovers the workspace + lists, stores creds, registers the webhook,
 *      and closes the popup (postMessage 'clickup-connected')
 *   4. We re-poll status → Connected, then let them pick a default list
 *
 * No API tokens or list IDs to paste.
 */

import { useState, useEffect, useCallback, useRef } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

export default function VulaClickUpConnect({ tenantId, tenantName }) {
  const [status, setStatus] = useState(null)   // null | connected | not_connected | error
  const [account, setAccount] = useState(null)
  const [lists, setLists] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/clickup/status/${tenantId}`)
      const d = await r.json()
      setStatus(d.status)
      setAccount(d)
      if (d.status === 'connected') {
        const lr = await fetch(`${VULA_API}/v1/clickup/lists/${tenantId}`)
        const ld = await lr.json()
        setLists(ld.lists || [])
      }
    } catch {
      setStatus('error')
    }
  }, [tenantId])

  useEffect(() => { loadStatus() }, [loadStatus])

  // Re-poll when the popup posts back, on window focus, and briefly on an interval.
  useEffect(() => {
    const onMsg = (e) => { if (e.data === 'clickup-connected') loadStatus() }
    const onFocus = () => loadStatus()
    window.addEventListener('message', onMsg)
    window.addEventListener('focus', onFocus)
    return () => {
      window.removeEventListener('message', onMsg)
      window.removeEventListener('focus', onFocus)
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [loadStatus])

  const handleConnect = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/clickup/authorize-url?tenant_id=${encodeURIComponent(tenantId)}`)
      const d = await r.json()
      if (!d.url) throw new Error(d.error || 'ClickUp app not configured.')
      window.open(d.url, 'clickup-oauth', 'width=620,height=760')
      // Poll for ~90s while the user authorises in the popup.
      let ticks = 0
      pollRef.current = setInterval(() => {
        loadStatus()
        if (++ticks > 30 || status === 'connected') clearInterval(pollRef.current)
      }, 3000)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [tenantId, loadStatus, status])

  const setDefaultList = async (listId) => {
    await fetch(`${VULA_API}/v1/clickup/default-list`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_id: tenantId, list_id: listId }),
    })
    loadStatus()
  }

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.icon}>🗂️</div>
        <div>
          <h3 style={styles.title}>ClickUp</h3>
          <p style={styles.subtitle}>Create &amp; track tasks from WhatsApp</p>
        </div>
        <StatusBadge status={status} />
      </div>

      {status === 'connected' && account && (
        <div style={styles.connectedInfo}>
          <div style={styles.infoRow}>
            <span style={styles.label}>Workspace</span>
            <span style={styles.value}>{account.team_id ? `#${account.team_id}` : '—'}</span>
          </div>
          <div style={styles.infoRow}>
            <span style={styles.label}>Default list</span>
            {lists.length ? (
              <select
                value={account.default_list_id || ''}
                onChange={(e) => setDefaultList(e.target.value)}
                style={styles.select}
              >
                {lists.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            ) : (
              <span style={styles.value}>{account.default_list_id || '—'}</span>
            )}
          </div>
        </div>
      )}

      {error && <div style={styles.errorBox}>{error}</div>}

      {status !== 'connected' ? (
        <div>
          <p style={styles.description}>
            Connect {tenantName || 'your'} ClickUp so you can create, list and update tasks — and
            set reminders — straight from WhatsApp. One click, no API keys.
          </p>
          <button
            onClick={handleConnect}
            disabled={loading}
            style={loading ? styles.btnDisabled : styles.btn}
          >
            {loading ? 'Opening ClickUp…' : '🔗 Connect ClickUp'}
          </button>
          <p style={styles.hint}>A ClickUp window opens for you to approve. Takes about 30 seconds.</p>
        </div>
      ) : (
        <button onClick={handleConnect} disabled={loading} style={styles.btnGhost}>
          {loading ? '…' : 'Reconnect'}
        </button>
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const configs = {
    connected: { label: 'Connected', color: '#22c55e', bg: 'rgba(34,197,94,0.15)' },
    error: { label: 'Error', color: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
    not_connected: { label: 'Not connected', color: '#6b7280', bg: 'rgba(107,114,128,0.15)' },
  }
  const c = configs[status] || configs.not_connected
  return <span style={{ ...styles.badge, color: c.color, background: c.bg }}>{c.label}</span>
}

const styles = {
  card: { background: '#111111', border: '1px solid #2a2a2a', borderRadius: 8, padding: 24, maxWidth: 480 },
  header: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 },
  icon: { fontSize: 32 },
  title: { margin: 0, color: '#f5f2ec', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: '#6b7280', fontSize: 13 },
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600 },
  connectedInfo: { background: '#0a0a0a', border: '1px solid #1a1a1a', borderRadius: 6, padding: 16, marginBottom: 16 },
  infoRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid #1a1a1a' },
  label: { color: '#6b7280', fontSize: 13 },
  value: { color: '#f5f2ec', fontSize: 13, fontWeight: 500 },
  select: { background: '#0a0a0a', color: '#f5f2ec', border: '1px solid #2a2a2a', borderRadius: 6, padding: '6px 8px', fontSize: 13, maxWidth: 240 },
  description: { color: '#9ca3af', fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' },
  errorBox: { background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 12 },
  btn: { background: '#7B68EE', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: '#2a2a2a', color: '#6b7280', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnGhost: { background: 'transparent', color: '#9ca3af', border: '1px solid #2a2a2a', borderRadius: 6, padding: '8px 16px', fontSize: 13, cursor: 'pointer' },
  hint: { color: '#4b5563', fontSize: 12, marginTop: 10, textAlign: 'center' },
}
