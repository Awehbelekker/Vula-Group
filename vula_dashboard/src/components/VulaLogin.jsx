/**
 * VulaLogin.jsx
 *
 * Supabase email/password auth for Vula Dashboard.
 *
 * After login, looks up vula_tenant_users to find the user's tenant + role:
 *   role = 'master'  → sees all tenants (Ian / Vula admin)
 *   role = 'owner'   → sees only their tenant (Off the Hook client, etc.)
 */

import { useState } from 'react'
import { supabase } from '../lib/supabase'
import { useAuthStore } from '../store/auth'

const COLORS = {
  bg: '#F7F4EE',
  surface: '#FFFFFF',
  border: '#DDD8CE',
  green: '#2C5545',
  amber: '#C4861A',
  muted: '#8A8680',
  charcoal: '#1E1E1E',
}

export default function VulaLogin({ onSuccess }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuthStore()

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      // 1 — Supabase Auth sign-in
      const { data, error: authErr } = await supabase.auth.signInWithPassword({ email, password })
      if (authErr) throw authErr

      const user = data.user

      // 2 — Look up tenant assignment
      const { data: assignments, error: dbErr } = await supabase
        .from('vula_tenant_users')
        .select('tenant_id, role')
        .eq('user_id', user.id)
        .limit(1)
        .single()

      if (dbErr || !assignments) {
        throw new Error('No tenant access found for this account. Contact Ian at Vula.')
      }

      // 3 — Store in auth
      login(
        { id: user.id, email: user.email, name: user.user_metadata?.full_name || email.split('@')[0] },
        assignments.tenant_id,
        assignments.role
      )

      onSuccess?.()
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.outer}>
      <div style={styles.card}>
        {/* Logo */}
        <div style={styles.logoWrap}>
          <span style={styles.logoText}>Vula</span>
          <span style={styles.logoSub}>Commerce Admin</span>
        </div>

        <h1 style={styles.heading}>Sign in</h1>
        <p style={styles.sub}>Use the email address Ian gave you access with.</p>

        {error && <div style={styles.errorBox}>{error}</div>}

        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.field}>
            <label style={styles.label}>Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
              style={styles.input}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              style={styles.input}
            />
          </div>

          <button type="submit" disabled={loading} style={loading ? styles.btnDisabled : styles.btn}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p style={styles.footer}>
          Don't have access?{' '}
          <a href="https://wa.me/27820000000?text=Hi+Ian,+I+need+access+to+Vula+Dashboard" target="_blank" rel="noopener noreferrer" style={styles.link}>
            Contact Vula
          </a>
        </p>
      </div>
    </div>
  )
}

const styles = {
  outer:       { minHeight: '100vh', background: COLORS.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 },
  card:        { background: COLORS.surface, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: 40, width: '100%', maxWidth: 400 },
  logoWrap:    { marginBottom: 32, display: 'flex', flexDirection: 'column', gap: 2 },
  logoText:    { fontFamily: "'Cormorant Garamond', serif", fontSize: 32, fontWeight: 700, color: COLORS.green },
  logoSub:     { fontFamily: 'system-ui', fontSize: 12, color: COLORS.muted, letterSpacing: '0.08em', textTransform: 'uppercase' },
  heading:     { fontFamily: "'Cormorant Garamond', serif", fontSize: 26, fontWeight: 700, color: COLORS.charcoal, margin: '0 0 4px' },
  sub:         { fontFamily: 'system-ui', fontSize: 13, color: COLORS.muted, margin: '0 0 24px' },
  errorBox:    { background: '#FEF2F2', border: '1px solid #FECACA', color: '#991B1B', borderRadius: 6, padding: '10px 14px', fontSize: 13, fontFamily: 'system-ui', marginBottom: 16 },
  form:        { display: 'flex', flexDirection: 'column', gap: 16 },
  field:       { display: 'flex', flexDirection: 'column', gap: 6 },
  label:       { fontFamily: 'system-ui', fontSize: 13, fontWeight: 600, color: COLORS.charcoal },
  input:       { padding: '10px 12px', border: `1px solid ${COLORS.border}`, borderRadius: 6, fontFamily: 'system-ui', fontSize: 14, color: COLORS.charcoal, background: COLORS.bg, outline: 'none' },
  btn:         { padding: '12px', background: COLORS.green, color: '#fff', border: 'none', borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  btnDisabled: { padding: '12px', background: '#DDD8CE', color: COLORS.muted, border: 'none', borderRadius: 6, fontSize: 14, cursor: 'not-allowed', fontFamily: 'system-ui' },
  footer:      { marginTop: 24, textAlign: 'center', fontFamily: 'system-ui', fontSize: 13, color: COLORS.muted },
  link:        { color: COLORS.green, textDecoration: 'underline' },
}
