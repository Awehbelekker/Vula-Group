/**
 * VulaLogin.jsx
 *
 * Handles three auth flows:
 *   1. Normal sign-in (email + password)
 *   2. Password recovery — Supabase puts #access_token in URL after clicking
 *      the reset email; we detect this and show a "set new password" form
 *   3. First-time invite — same token flow as recovery
 *
 * Master user (Richard/Ian): awehbelekker@gmail.com
 * role = 'master'  → sees all tenants
 * role = 'owner'   → client, sees only their tenant
 */

import { useState, useEffect } from 'react'
import { supabase } from '../lib/supabase'
import { useAuthStore } from '../store/auth'
import { resolveTenantFromHost, getTenantTheme } from '../theme/tenantThemes'

// If reached via a tenant subdomain (offthehook.vula-ai.com), brand the login.
const LOGIN_TENANT = resolveTenantFromHost()
const LOGIN_THEME = LOGIN_TENANT ? getTenantTheme(LOGIN_TENANT) : null

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
  const [mode, setMode] = useState('login')      // 'login' | 'reset_request' | 'set_password'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuthStore()

  // Detect Supabase recovery / invite token in URL hash on mount
  useEffect(() => {
    const hash = window.location.hash
    if (hash.includes('type=recovery') || hash.includes('type=invite')) {
      // Supabase has already set a session from the token — switch to set-password form
      setMode('set_password')
      setInfo('Enter a new password for your account.')
      // Clean URL
      window.history.replaceState(null, '', window.location.pathname)
    }
  }, [])

  // ── Normal sign-in ──────────────────────────────────────────────────────────
  async function handleSignIn(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { data, error: authErr } = await supabase.auth.signInWithPassword({ email, password })
      if (authErr) throw authErr

      const user = data.user
      const { data: assignment, error: dbErr } = await supabase
        .from('vula_tenant_users')
        .select('tenant_id, role')
        .eq('user_id', user.id)
        .limit(1)
        .single()

      if (dbErr || !assignment) {
        throw new Error('No tenant access found. Contact Richard at Vula.')
      }

      login(
        { id: user.id, email: user.email, name: user.user_metadata?.full_name || email.split('@')[0] },
        assignment.tenant_id,
        assignment.role,
      )
      onSuccess?.()
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  // ── Request password reset email ────────────────────────────────────────────
  async function handleResetRequest(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { error: err } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}${window.location.pathname}`,
      })
      if (err) throw err
      setInfo(`Reset email sent to ${email}. Check your inbox and click the link.`)
      setMode('login')
    } catch (err) {
      setError(err.message || 'Could not send reset email')
    } finally {
      setLoading(false)
    }
  }

  // ── Set new password (after clicking recovery link) ─────────────────────────
  async function handleSetPassword(e) {
    e.preventDefault()
    setError('')
    if (newPassword !== confirmPassword) { setError('Passwords do not match.'); return }
    if (newPassword.length < 8) { setError('Password must be at least 8 characters.'); return }
    setLoading(true)
    try {
      const { data, error: err } = await supabase.auth.updateUser({ password: newPassword })
      if (err) throw err

      // Now get tenant assignment
      const user = data.user
      const { data: assignment, error: dbErr } = await supabase
        .from('vula_tenant_users')
        .select('tenant_id, role')
        .eq('user_id', user.id)
        .limit(1)
        .single()

      if (dbErr || !assignment) {
        throw new Error('Password set! Now sign in with your new password.')
      }

      login(
        { id: user.id, email: user.email, name: user.user_metadata?.full_name || user.email.split('@')[0] },
        assignment.tenant_id,
        assignment.role,
      )
      onSuccess?.()
    } catch (err) {
      // If error mentions signing in, switch back to login with a success message
      if (err.message?.includes('sign in')) {
        setInfo(err.message)
        setMode('login')
      } else {
        setError(err.message || 'Could not set password')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={s.outer}>
      <div style={s.card}>
        <div style={s.logoWrap}>
          {LOGIN_THEME ? (
            <>
              {LOGIN_THEME.logoUrl ? (
                <img src={LOGIN_THEME.logoUrl} alt={LOGIN_THEME.name}
                     style={{ height: 40, width: 'auto', objectFit: 'contain', marginBottom: 6 }} />
              ) : (
                <span style={{ ...s.logoText, color: LOGIN_THEME.accent }}>{LOGIN_THEME.name}</span>
              )}
              <span style={s.logoSub}>Powered by Vula</span>
            </>
          ) : (
            <>
              <span style={s.logoText}>Vula</span>
              <span style={s.logoSub}>Business Dashboard</span>
            </>
          )}
        </div>

        {mode === 'login' && (
          <>
            <h1 style={s.heading}>Sign in</h1>
            {info && <div style={s.infoBox}>{info}</div>}
            {error && <div style={s.errorBox}>{error}</div>}
            <form onSubmit={handleSignIn} style={s.form}>
              <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
              <Field label="Password" type="password" value={password} onChange={setPassword} placeholder="••••••••" />
              <Btn disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</Btn>
            </form>
            <p style={s.footer}>
              <button style={s.textBtn} onClick={() => { setMode('reset_request'); setError(''); setInfo('') }}>
                Forgot password?
              </button>
            </p>
          </>
        )}

        {mode === 'reset_request' && (
          <>
            <h1 style={s.heading}>Reset password</h1>
            <p style={s.sub}>We'll email you a link to set a new password.</p>
            {error && <div style={s.errorBox}>{error}</div>}
            <form onSubmit={handleResetRequest} style={s.form}>
              <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
              <Btn disabled={loading}>{loading ? 'Sending…' : 'Send reset email'}</Btn>
            </form>
            <p style={s.footer}>
              <button style={s.textBtn} onClick={() => { setMode('login'); setError('') }}>
                ← Back to sign in
              </button>
            </p>
          </>
        )}

        {mode === 'set_password' && (
          <>
            <h1 style={s.heading}>Set your password</h1>
            {info && <div style={s.infoBox}>{info}</div>}
            {error && <div style={s.errorBox}>{error}</div>}
            <form onSubmit={handleSetPassword} style={s.form}>
              <Field label="New password" type="password" value={newPassword} onChange={setNewPassword} placeholder="At least 8 characters" />
              <Field label="Confirm password" type="password" value={confirmPassword} onChange={setConfirmPassword} placeholder="Repeat your password" />
              <Btn disabled={loading}>{loading ? 'Saving…' : 'Set password & sign in'}</Btn>
            </form>
          </>
        )}
      </div>
    </div>
  )
}

function Field({ label, type, value, onChange, placeholder }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label style={s.label}>{label}</label>
      <input
        type={type} required value={value} placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        style={s.input}
        autoComplete={type === 'password' ? 'current-password' : 'email'}
      />
    </div>
  )
}

function Btn({ children, disabled }) {
  const activeStyle = LOGIN_THEME
    ? { ...s.btn, background: LOGIN_THEME.accent }
    : s.btn
  return (
    <button type="submit" disabled={disabled}
      style={disabled ? s.btnDisabled : activeStyle}>
      {children}
    </button>
  )
}

const s = {
  outer:       { minHeight: '100vh', background: COLORS.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 },
  card:        { background: COLORS.surface, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: 40, width: '100%', maxWidth: 400 },
  logoWrap:    { marginBottom: 32, display: 'flex', flexDirection: 'column', gap: 2 },
  logoText:    { fontSize: 32, fontWeight: 700, color: COLORS.green, fontFamily: 'system-ui' },
  logoSub:     { fontSize: 12, color: COLORS.muted, letterSpacing: '0.08em', textTransform: 'uppercase', fontFamily: 'system-ui' },
  heading:     { fontSize: 22, fontWeight: 700, color: COLORS.charcoal, margin: '0 0 4px', fontFamily: 'system-ui' },
  sub:         { fontSize: 13, color: COLORS.muted, margin: '0 0 20px', fontFamily: 'system-ui' },
  infoBox:     { background: '#F0FDF4', border: '1px solid #86EFAC', color: '#166534', borderRadius: 6, padding: '10px 14px', fontSize: 13, fontFamily: 'system-ui', marginBottom: 16 },
  errorBox:    { background: '#FEF2F2', border: '1px solid #FECACA', color: '#991B1B', borderRadius: 6, padding: '10px 14px', fontSize: 13, fontFamily: 'system-ui', marginBottom: 16 },
  form:        { display: 'flex', flexDirection: 'column', gap: 16 },
  label:       { fontSize: 13, fontWeight: 600, color: COLORS.charcoal, fontFamily: 'system-ui' },
  input:       { padding: '10px 12px', border: `1px solid ${COLORS.border}`, borderRadius: 6, fontSize: 14, color: COLORS.charcoal, background: COLORS.bg, outline: 'none', fontFamily: 'system-ui' },
  btn:         { padding: '12px', background: COLORS.green, color: '#fff', border: 'none', borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  btnDisabled: { padding: '12px', background: '#DDD8CE', color: COLORS.muted, border: 'none', borderRadius: 6, fontSize: 14, cursor: 'not-allowed', fontFamily: 'system-ui' },
  footer:      { marginTop: 20, textAlign: 'center' },
  textBtn:     { background: 'none', border: 'none', color: COLORS.green, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui', textDecoration: 'underline' },
}
