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
 * No API tokens or list IDs to paste — but a ClickUp personal token is offered as the way round
 * a popup that never comes back (2026-10-03: since August the OAuth callback was never reached,
 * while Vula's old sign-in had lost the workspace). A refused sign-in shows as
 * "Needs reconnecting" (backend: needs_reconnect) instead of a green "Connected".
 *
 * Data and actions: settings/connect/useClickUpConnect.js (shared with the redesign's ClickUpConnectV2).
 */

import { timeAgo } from './settings/connect/useOAuthConnect'
import { useClickUpConnect } from './settings/connect/useClickUpConnect'


export default function VulaClickUpConnect({ tenantId, tenantName }) {
  const { status, account, lists, loading, error, notice, showToken, setShowToken, token, setToken,
    handleConnect, connectWithToken, refileMissing, setDefaultList } = useClickUpConnect(tenantId)

  // Shown connected or not: on 3 Oct the panel still read "Connected" on a dead token, so the
  // only way to reconnect was the popup — which needs CLICKUP_CLIENT_ID, missing on Railway.
  const tokenForm = !showToken ? (
    <button type="button" onClick={() => setShowToken(true)} style={styles.linkBtn}>
      {status === 'connected' ? 'Reconnect with a ClickUp token' : "Window didn't come back? Connect with a ClickUp token instead"}
    </button>
  ) : (
    <div style={{ marginTop: 10 }}>
      <p style={styles.hint}>
        In ClickUp: your avatar › Settings › Apps › API Token › Generate, then copy it (starts with <code>pk_</code>).
      </p>
      <input
        type="password" value={token} onChange={(e) => setToken(e.target.value)}
        placeholder="pk_…" autoComplete="off" style={{ ...styles.select, width: '100%', marginBottom: 8 }}
      />
      <button onClick={connectWithToken} disabled={loading || !token.trim()}
              style={loading || !token.trim() ? styles.btnDisabled : styles.btn}>
        {loading ? 'Checking…' : 'Connect with token'}
      </button>
    </div>
  )

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
          <SyncHealthRow lastSyncedAt={account.last_synced_at} lastSyncStatus={account.last_sync_status} lastSyncError={account.last_sync_error} />
        </div>
      )}

      {status === 'needs_reconnect' && (
        <div style={styles.errorBox}>
          <strong>ClickUp has stopped accepting Vula's sign-in.</strong>{' '}
          {account?.last_sync_error ? `(${String(account.last_sync_error).slice(0, 120)}) ` : ''}
          Documents are still filed in Vula, and are sent to ClickUp once you reconnect.
        </div>
      )}
      {error && <div style={styles.errorBox}>{error}</div>}
      {notice && <div style={styles.noticeBox}>{notice}</div>}

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
          {tokenForm}
        </div>
      ) : (
        <div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={handleConnect} disabled={loading} style={styles.btnGhost}>
              {loading ? '…' : 'Reconnect'}
            </button>
            <button onClick={refileMissing} style={styles.btnGhost}>Send missed documents</button>
          </div>
          {tokenForm}
        </div>
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
  const configs = {
    connected: { label: 'Connected', color: 'var(--ok)', bg: 'rgba(34,197,94,0.15)' },
    error: { label: 'Error', color: 'var(--danger)', bg: 'rgba(239,68,68,0.15)' },
    needs_reconnect: { label: 'Needs reconnecting', color: 'var(--danger)', bg: 'rgba(239,68,68,0.15)' },
    not_connected: { label: 'Not connected', color: 'var(--muted)', bg: 'rgba(107,114,128,0.15)' },
  }
  const c = configs[status] || configs.not_connected
  return <span style={{ ...styles.badge, color: c.color, background: c.bg }}>{c.label}</span>
}

const styles = {
  card: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 24, maxWidth: 480 },
  header: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 },
  icon: { fontSize: 32 },
  title: { margin: 0, color: 'var(--ink)', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: 'var(--muted)', fontSize: 13 },
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600 },
  connectedInfo: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: 16, marginBottom: 16 },
  infoRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border-soft)' },
  label: { color: 'var(--muted)', fontSize: 13 },
  value: { color: 'var(--ink)', fontSize: 13, fontWeight: 500 },
  select: { background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 8px', fontSize: 13, maxWidth: 240 },
  description: { color: 'var(--muted)', fontSize: 14, lineHeight: 1.6, margin: '0 0 16px' },
  errorBox: { background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--danger)', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 12 },
  btn: { background: '#7B68EE', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: 'var(--surface-alt)', color: 'var(--muted)', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnGhost: { background: 'transparent', color: 'var(--faint)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 16px', fontSize: 13, cursor: 'pointer' },
  noticeBox: { background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', color: 'var(--ok)', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 12 },
  linkBtn: { display: 'block', margin: '8px auto 0', background: 'none', border: 'none', color: 'var(--accent)', fontSize: 12, textDecoration: 'underline', cursor: 'pointer' },
  hint: { color: 'var(--muted)', fontSize: 12, marginTop: 10, textAlign: 'center' },
}
