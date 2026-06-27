/**
 * VulaEmailConnect.jsx — connect any IMAP/SMTP mailbox (GoDaddy, cPanel, Zoho…).
 * Credentials form (not OAuth). Presets autofill common hosts. Draft-only by default.
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
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [form, setForm] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/email/status/${tenantId}`)
      const d = await r.json(); setStatus(d.status); setAccount(d)
    } catch { setStatus('error') }
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
      if (d.status === 'connected') { setForm(blank); loadStatus() }
      else setError(d.error || 'Could not connect — check the host, port and (app) password.')
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  const disconnect = async () => {
    if (!confirm('Disconnect this mailbox?')) return
    await fetch(`${VULA_API}/v1/email/disconnect/${tenantId}`, { method: 'DELETE' })
    setStatus('not_connected'); setAccount(null)
  }

  const inp = { width: '100%', padding: '9px 11px', border: '1px solid #2a2a2a', borderRadius: 6, fontSize: 13, color: '#f5f2ec', background: '#0a0a0a', boxSizing: 'border-box' }

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.icon}>✉️</div>
        <div><h3 style={styles.title}>Email (IMAP / SMTP)</h3>
          <p style={styles.subtitle}>GoDaddy, cPanel, Zoho — any mailbox</p></div>
        <span style={{ ...styles.badge, color: status === 'connected' ? '#22c55e' : '#6b7280',
          background: status === 'connected' ? 'rgba(34,197,94,0.15)' : 'rgba(107,114,128,0.15)' }}>
          {status === 'connected' ? 'Connected' : 'Not connected'}</span>
      </div>

      {status === 'connected' && account ? (
        <div>
          <div style={styles.info}><span style={styles.label}>Mailbox</span><span style={styles.value}>{account.email}</span></div>
          <div style={styles.info}><span style={styles.label}>Mode</span><span style={styles.value}>{account.send_mode === 'send' ? 'Sends directly' : 'Draft-only (you send)'}</span></div>
          <button onClick={disconnect} style={styles.btnDanger}>Disconnect</button>
        </div>
      ) : (
        <div>
          <p style={styles.desc}>Use an <strong>app password</strong> (not your main login) where your provider supports it — it's revocable and scoped.</p>
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
          <button onClick={connect} disabled={busy} style={busy ? styles.btnDisabled : styles.btn}>
            {busy ? 'Testing connection…' : '🔗 Connect mailbox'}
          </button>
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
  badge: { marginLeft: 'auto', padding: '4px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600 },
  info: { display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #1a1a1a' },
  label: { color: '#6b7280', fontSize: 13 }, value: { color: '#f5f2ec', fontSize: 13, fontWeight: 500 },
  desc: { color: '#9ca3af', fontSize: 13, lineHeight: 1.5, margin: '0 0 12px' },
  errorBox: { background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 10 },
  btn: { background: '#C4861A', color: '#fff', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%' },
  btnDisabled: { background: '#2a2a2a', color: '#6b7280', border: 'none', borderRadius: 6, padding: '12px 24px', fontSize: 14, cursor: 'not-allowed', width: '100%' },
  btnDanger: { marginTop: 14, background: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, padding: '8px 16px', fontSize: 13, cursor: 'pointer' },
}
