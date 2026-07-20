/**
 * VulaEmailConnect.jsx — connect one or more IMAP/SMTP mailboxes (GoDaddy, cPanel, Zoho, Gmail…).
 * Credentials form (not OAuth). Presets autofill common hosts. Draft-only by default.
 *
 * A tenant can connect several mailboxes (migration 093) — e.g. admin@ and sales@ and a
 * personal Gmail, all feeding the same KB/contacts/document filing. Exactly one is "primary",
 * used by the handful of features that only make sense against one mailbox (the AI email
 * skill's default, bank-statement password handling).
 */
import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const PRESETS = {
  'GoDaddy Workspace': { imap_host: 'imap.secureserver.net', imap_port: 993, smtp_host: 'smtpout.secureserver.net', smtp_port: 465 },
  'cPanel / hosting':  { imap_host: '', imap_port: 993, smtp_host: '', smtp_port: 465 },
  'Zoho':              { imap_host: 'imap.zoho.com', imap_port: 993, smtp_host: 'smtp.zoho.com', smtp_port: 465 },
  'Gmail (app pwd)':   { imap_host: 'imap.gmail.com', imap_port: 993, smtp_host: 'smtp.gmail.com', smtp_port: 465 },
  'Office 365':        { imap_host: 'outlook.office365.com', imap_port: 993, smtp_host: 'smtp.office365.com', smtp_port: 587 },
}
const blank = { email: '', password: '', from_name: '', imap_host: '', imap_port: 993, smtp_host: '', smtp_port: 465, send_mode: 'draft' }

export default function VulaEmailConnect({ tenantId, adminEmail }) {
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [busyAccountId, setBusyAccountId] = useState(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/email/status/${tenantId}`)
      const d = await r.json()
      setAccounts(d.accounts || [])
    } catch { setAccounts([]) }
    setLoading(false)
  }, [tenantId])
  useEffect(() => { loadStatus() }, [loadStatus])

  const applyPreset = (name) => setForm(f => ({ ...f, ...(PRESETS[name] || {}) }))

  const connect = async () => {
    if (!form.email || !form.password || !form.imap_host) { setError('Email, password and IMAP host are required.'); return }
    setBusy(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/email/connect`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId, connected_by: adminEmail, ...form,
          imap_port: Number(form.imap_port), smtp_port: Number(form.smtp_port) }),
      })
      const d = await r.json()
      if (d.status === 'connected') { setForm(blank); setShowForm(false); loadStatus() }
      else setError(d.error || 'Could not connect — check the host, port and (app) password.')
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const disconnect = async (accountId) => {
    if (!confirm('Disconnect this mailbox?')) return
    setBusyAccountId(accountId)
    await fetch(`${VULA_API}/v1/email/disconnect/${tenantId}/${accountId}`, { method: 'DELETE' })
    setBusyAccountId(null)
    loadStatus()
  }

  const setPrimary = async (accountId) => {
    setBusyAccountId(accountId)
    await fetch(`${VULA_API}/v1/email/set-primary/${tenantId}/${accountId}`, { method: 'POST' })
    setBusyAccountId(null)
    loadStatus()
  }

  const backfill = async (accountId) => {
    setBusyAccountId(accountId)
    await fetch(`${VULA_API}/v1/email/backfill/${tenantId}?account_id=${accountId}`, { method: 'POST' })
    setBusyAccountId(null)
    alert('Backfill started in the background — check Contacts/Documents in a few minutes.')
  }

  const inp = { width: '100%', padding: '9px 11px', border: '1px solid #2a2a2a', borderRadius: 6, fontSize: 13, color: '#f5f2ec', background: '#0a0a0a', boxSizing: 'border-box' }

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.icon}>✉️</div>
        <div><h3 style={styles.title}>Email (IMAP / SMTP)</h3>
          <p style={styles.subtitle}>Connect as many mailboxes as you need — admin@, sales@, a personal Gmail…</p></div>
        <span style={{ ...styles.badge, color: accounts.length ? '#22c55e' : '#6b7280',
          background: accounts.length ? 'rgba(34,197,94,0.15)' : 'rgba(107,114,128,0.15)' }}>
          {accounts.length ? `${accounts.length} connected` : 'None connected'}</span>
      </div>

      {loading ? <p style={styles.desc}>Loading…</p> : accounts.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          {accounts.map(a => (
            <div key={a.id} style={styles.acctRow}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={styles.value}>{a.email}</span>
                  {a.is_primary && <span style={styles.primaryBadge}>Primary</span>}
                </div>
                <div style={styles.subvalue}>
                  {a.send_mode === 'send' ? 'Sends directly' : 'Draft-only (you send)'}
                  {a.last_sync_at ? ` · last synced ${new Date(a.last_sync_at).toLocaleString()}` : ' · not yet synced'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                {!a.is_primary && (
                  <button onClick={() => setPrimary(a.id)} disabled={busyAccountId === a.id} style={styles.btnSmall}>Make primary</button>
                )}
                <button onClick={() => backfill(a.id)} disabled={busyAccountId === a.id} style={styles.btnSmall}>Backfill</button>
                <button onClick={() => disconnect(a.id)} disabled={busyAccountId === a.id} style={styles.btnDangerSmall}>Disconnect</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {!showForm ? (
        <button onClick={() => setShowForm(true)} style={styles.btn}>
          {accounts.length ? '+ Connect another mailbox' : '🔗 Connect a mailbox'}
        </button>
      ) : (
        <div>
          <p style={styles.desc}>Use an <strong>app password</strong> (not your main login) where your provider supports it — it's revocable and scoped. Gmail requires one if 2FA is on.</p>
          <select onChange={e => applyPreset(e.target.value)} style={{ ...inp, marginBottom: 8 }} defaultValue="">
            <option value="" disabled>Quick setup (autofill hosts)…</option>
            {Object.keys(PRESETS).map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <input style={{ ...inp, marginBottom: 8 }} placeholder="Email address" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
          <input style={{ ...inp, marginBottom: 8 }} type="password" placeholder="Password / app-password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} />
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <input style={inp} placeholder="IMAP host" value={form.imap_host} onChange={e => setForm({ ...form, imap_host: e.target.value })} />
            <input style={{ ...inp, width: 80 }} placeholder="993" value={form.imap_port} onChange={e => setForm({ ...form, imap_port: e.target.value })} />
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <input style={inp} placeholder="SMTP host (for sending)" value={form.smtp_host} onChange={e => setForm({ ...form, smtp_host: e.target.value })} />
            <input style={{ ...inp, width: 80 }} placeholder="465" value={form.smtp_port} onChange={e => setForm({ ...form, smtp_port: e.target.value })} />
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
            <input style={inp} placeholder="From name (optional)" value={form.from_name} onChange={e => setForm({ ...form, from_name: e.target.value })} />
            <select style={{ ...inp, width: 150 }} value={form.send_mode} onChange={e => setForm({ ...form, send_mode: e.target.value })}>
              <option value="draft">Draft-only</option><option value="send">Send directly</option>
            </select>
          </div>
          {error && <div style={styles.errorBox}>{error}</div>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={connect} disabled={busy} style={busy ? styles.btnDisabled : styles.btn}>
              {busy ? 'Testing connection…' : '🔗 Connect mailbox'}
            </button>
            <button onClick={() => { setShowForm(false); setError(null); setForm(blank) }} style={styles.btnCancel}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

const styles = {
  card: { background: '#111111', border: '1px solid #2a2a2a', borderRadius: 8, padding: 24, maxWidth: 480 },
  header: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 },
  icon: { fontSize: 32 }, title: { margin: 0, color: '#f5f2ec', fontSize: 18, fontWeight: 600 },
  subtitle: { margin: '2px 0 0', color: '#6b7280', fontSize: 13 },
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' },
  acctRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', borderBottom: '1px solid #1a1a1a' },
  primaryBadge: { fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: '#C4861A', background: 'rgba(196,134,26,0.15)', padding: '2px 6px', borderRadius: 4 },
  label: { color: '#6b7280', fontSize: 13 }, value: { color: '#f5f2ec', fontSize: 13, fontWeight: 500 },
  subvalue: { color: '#6b7280', fontSize: 11.5, marginTop: 2 },
  desc: { color: '#9ca3af', fontSize: 13, lineHeight: 1.5, margin: '0 0 12px' },
  errorBox: { background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 10 },
  btn: { background: '#C4861A', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: '#2a2a2a', color: '#6b7280', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnCancel: { background: 'transparent', color: '#6b7280', border: '1px solid #2a2a2a', borderRadius: 6, padding: '12px 18px', fontSize: 13, cursor: 'pointer' },
  btnSmall: { background: 'transparent', color: '#9ca3af', border: '1px solid #2a2a2a', borderRadius: 6, padding: '5px 10px', fontSize: 11.5, cursor: 'pointer', whiteSpace: 'nowrap' },
  btnDangerSmall: { background: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, padding: '5px 10px', fontSize: 11.5, cursor: 'pointer', whiteSpace: 'nowrap' },
}
