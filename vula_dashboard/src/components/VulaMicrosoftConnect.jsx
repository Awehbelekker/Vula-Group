/**
 * VulaMicrosoftConnect.jsx — one-click "Connect Microsoft" (OneDrive + Outlook), draft-only mail.
 * Mirrors VulaGoogleConnect: popup OAuth → backend callback → status poll.
 * Flow: settings/connect/useOAuthConnect.js (shared with the redesign's OAuthConnectV2).
 */
import { timeAgo, useOAuthConnect } from './settings/connect/useOAuthConnect'


export default function VulaMicrosoftConnect({ tenantId, tenantName }) {
  const { status, account, loading, error, handleConnect } = useOAuthConnect({ tenantId, provider: 'microsoft', popupName: 'ms-oauth', label: 'Microsoft' })

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
          <SyncHealthRow lastSyncedAt={account.last_synced_at} lastSyncStatus={account.last_sync_status} lastSyncError={account.last_sync_error} />
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

// Sync HEALTH is distinct from OAuth connection status — a tenant can stay "Connected" (a valid
// token) while the background sync has been silently failing for days (migration 169, go-live
// readiness pass Phase 3.2). Shows nothing until at least one sync has actually run.
function SyncHealthRow({ lastSyncedAt, lastSyncStatus, lastSyncError }) {
  if (!lastSyncedAt) return null
  const failing = lastSyncStatus === 'error'
  return (
    <div style={{ ...styles.infoRow, borderBottom: 'none' }}>
      <span style={styles.label}>Sync</span>
      <span style={{ ...styles.value, color: failing ? 'var(--danger)' : styles.value.color }}>
        {failing ? `Failing — ${timeAgo(lastSyncedAt)}` : `Last synced ${timeAgo(lastSyncedAt)}`}
        {failing && lastSyncError && (
          <span style={{ display: 'block', fontSize: 11, color: 'var(--danger)', fontWeight: 400, marginTop: 2 }}>
            {String(lastSyncError).slice(0, 120)}
          </span>
        )}
      </span>
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
  btn: { background: '#0078D4', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: 'var(--surface-alt)', color: 'var(--muted)', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnGhost: { background: 'transparent', color: 'var(--faint)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 16px', fontSize: 13, cursor: 'pointer' },
  hint: { color: 'var(--muted)', fontSize: 12, marginTop: 10, textAlign: 'center' },
}
