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
const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const COLORS = {
  bg: '#F7F4EE',
  surface: '#FFFFFF',
  border: '#DDD8CE',
  green: 'var(--accent)',
  amber: '#C4861A',
  muted: '#8A8680',
  charcoal: '#1E1E1E',
}

export default function VulaLogin({ onSuccess }) {
  const [mode, setMode] = useState('login')      // 'login' | 'reset_request' | 'set_password' | 'signup' | 'setup_workspace'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [loading, setLoading] = useState(false)
  const [brandLogoUrl, setBrandLogoUrl] = useState(null)
  const { login } = useAuthStore()

  // Self-serve workspace setup (2026-08-15) — a signed-in Supabase user with no
  // vula_tenant_users row lands here instead of the old dead-end error, via resolveAndLogin below.
  const [pendingUser, setPendingUser] = useState(null)
  const [businessName, setBusinessName] = useState('')
  const [businessType, setBusinessType] = useState('other')
  const [businessTypes, setBusinessTypes] = useState([])

  useEffect(() => {
    fetch(`${VULA_API}/v1/tenants/registry`)
      .then((r) => r.json())
      .then((d) => { if (d?.business_types?.length) setBusinessTypes(d.business_types) })
      .catch(() => {})
  }, [])

  // Real logo (2026-07-24) — LOGIN_THEME's logoUrl is a static fallback in tenantThemes.js that
  // goes stale the moment a tenant uploads their own (confirmed: off-the-hook's real logo_url in
  // commerce_invoice_settings is a completely different file). Same fix already applied to the
  // post-login sidebar (App.jsx) — this is the pre-auth screen that never got it. Uses the public
  // /brand endpoint (not /admin/invoice-settings) since nobody is signed in yet at this screen.
  useEffect(() => {
    if (!LOGIN_TENANT) return
    fetch(`${VULA_API}/v1/commerce/${LOGIN_TENANT}/brand`)
      .then((r) => r.json())
      .then((b) => { if (b?.logo_url) setBrandLogoUrl(b.logo_url) })
      .catch(() => {})
  }, [])

  // Resolve tenant/role for a signed-in user (any method) and enter the app. A user with NO
  // vula_tenant_users row isn't necessarily a dead end anymore — self-serve signup lands here
  // too (Google OAuth, password, magic link all funnel through this same function), so instead
  // of throwing, offer them the chance to create their own workspace right here.
  async function resolveAndLogin(user) {
    const { data: assignment, error: dbErr } = await supabase
      .from('vula_tenant_users')
      .select('tenant_id, role')
      .eq('user_id', user.id)
      .limit(1)
      .single()
    if (dbErr || !assignment) {
      setPendingUser(user)
      setBusinessName('')
      setError('')
      setMode('setup_workspace')
      return
    }
    login(
      { id: user.id, email: user.email, name: user.user_metadata?.full_name || (user.email || '').split('@')[0] },
      assignment.tenant_id,
      assignment.role,
    )
    onSuccess?.()
  }

  // On mount: recovery/invite token → set-password; otherwise an existing session
  // (returning from Google / magic-link) → resolve + enter the app.
  useEffect(() => {
    const hash = window.location.hash
    if (hash.includes('type=recovery') || hash.includes('type=invite')) {
      setMode('set_password')
      setInfo('Enter a new password for your account.')
      window.history.replaceState(null, '', window.location.pathname)
      return
    }
    supabase.auth.getSession().then(({ data }) => {
      if (data?.session?.user) resolveAndLogin(data.session.user).catch((err) => setError(err.message))
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      if (session?.user) resolveAndLogin(session.user).catch((err) => setError(err.message))
    })
    return () => sub?.subscription?.unsubscribe?.()
  }, [])  // eslint-disable-line

  // ── Normal sign-in (email + password) ───────────────────────────────────────
  async function handleSignIn(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { data, error: authErr } = await supabase.auth.signInWithPassword({ email, password })
      if (authErr) throw authErr
      await resolveAndLogin(data.user)
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  // ── Create account (self-serve signup) ───────────────────────────────────────
  async function handleSignUp(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { data, error: authErr } = await supabase.auth.signUp({ email, password })
      if (authErr) throw authErr
      if (data.session) {
        await resolveAndLogin(data.user)
      } else {
        // Email confirmation is required on this project — no session yet. They'll land back
        // here via the confirmation link, at which point the mount-effect's getSession() call
        // picks them up and resolveAndLogin routes them into workspace setup automatically.
        setInfo(`Check ${email} for a confirmation link, then come back here and sign in.`)
        setMode('login')
      }
    } catch (err) {
      setError(err.message || 'Could not create your account')
    } finally {
      setLoading(false)
    }
  }

  // ── Create the new tenant + become its owner ─────────────────────────────────
  async function handleSetupWorkspace(e) {
    e.preventDefault()
    setError('')
    if (!businessName.trim()) { setError('Please enter your business name.'); return }
    setLoading(true)
    try {
      const { data: sessionData } = await supabase.auth.getSession()
      const token = sessionData?.session?.access_token
      const resp = await fetch(`${VULA_API}/v1/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ tenant_id: businessName, display_name: businessName, business_type: businessType }),
      })
      const data = await resp.json()
      if (!resp.ok) throw new Error(data.detail || `Server error ${resp.status}`)
      login(
        { id: pendingUser.id, email: pendingUser.email,
          name: pendingUser.user_metadata?.full_name || (pendingUser.email || '').split('@')[0] },
        data.tenant.tenant_id,
        data.role,
      )
      onSuccess?.()
    } catch (err) {
      setError(err.message || 'Could not set up your workspace')
    } finally {
      setLoading(false)
    }
  }

  // ── Continue with Google (one-click) ─────────────────────────────────────────
  async function handleGoogle() {
    setError('')
    const { error: err } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}${window.location.pathname}` },
    })
    if (err) setError(err.message)
  }

  // ── Magic link (passwordless) ────────────────────────────────────────────────
  async function handleMagicLink() {
    if (!email) { setError('Enter your email first, then tap “Email me a link”.'); return }
    setError(''); setLoading(true)
    try {
      const { error: err } = await supabase.auth.signInWithOtp({
        email, options: { emailRedirectTo: `${window.location.origin}${window.location.pathname}` },
      })
      if (err) throw err
      setInfo(`Magic link sent to ${email}. Open it on this device to sign in.`)
    } catch (err) {
      setError(err.message || 'Could not send the link')
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
              {(brandLogoUrl || LOGIN_THEME.logoUrl) ? (
                <img src={brandLogoUrl || LOGIN_THEME.logoUrl} alt={LOGIN_THEME.name}
                     style={{ height: 96, width: 'auto', maxWidth: '100%', objectFit: 'contain', marginBottom: 10 }} />
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
            <button type="button" onClick={handleGoogle} style={s.googleBtn}>
              <span style={{ fontWeight: 700, marginRight: 8, color: '#4285F4' }}>G</span> Continue with Google
            </button>
            <div style={s.divider}><span style={s.dividerText}>or</span></div>
            <form onSubmit={handleSignIn} style={s.form}>
              <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
              <Field label="Password" type="password" value={password} onChange={setPassword} placeholder="••••••••" />
              <Btn disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</Btn>
            </form>
            <p style={s.footer}>
              <button style={s.textBtn} onClick={handleMagicLink}>Email me a link</button>
              <span style={{ color: '#CFC9BE', margin: '0 8px' }}>·</span>
              <button style={s.textBtn} onClick={() => { setMode('reset_request'); setError(''); setInfo('') }}>
                Forgot password?
              </button>
            </p>
            <p style={s.footer}>
              New here?{' '}
              <button style={s.textBtn} onClick={() => { setMode('signup'); setError(''); setInfo('') }}>
                Create an account
              </button>
            </p>
          </>
        )}

        {mode === 'signup' && (
          <>
            <h1 style={s.heading}>Create your account</h1>
            <p style={s.sub}>Next you'll set up your business workspace.</p>
            {error && <div style={s.errorBox}>{error}</div>}
            <button type="button" onClick={handleGoogle} style={s.googleBtn}>
              <span style={{ fontWeight: 700, marginRight: 8, color: '#4285F4' }}>G</span> Continue with Google
            </button>
            <div style={s.divider}><span style={s.dividerText}>or</span></div>
            <form onSubmit={handleSignUp} style={s.form}>
              <Field label="Email" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
              <Field label="Password" type="password" value={password} onChange={setPassword} placeholder="At least 8 characters" />
              <Btn disabled={loading}>{loading ? 'Creating account…' : 'Create account'}</Btn>
            </form>
            <p style={s.footer}>
              <button style={s.textBtn} onClick={() => { setMode('login'); setError('') }}>
                ← Back to sign in
              </button>
            </p>
          </>
        )}

        {mode === 'setup_workspace' && (
          <>
            <h1 style={s.heading}>Set up your workspace</h1>
            <p style={s.sub}>Signed in as {pendingUser?.email}. What's your business called?</p>
            {error && <div style={s.errorBox}>{error}</div>}
            <form onSubmit={handleSetupWorkspace} style={s.form}>
              <Field label="Business name" type="text" value={businessName} onChange={setBusinessName}
                     placeholder="e.g. Off the Hook" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <label style={s.label}>What kind of business?</label>
                <select value={businessType} onChange={(e) => setBusinessType(e.target.value)} style={s.input}>
                  {(businessTypes.length ? businessTypes : [{ id: 'other', label: 'Other / General' }])
                    .map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                </select>
              </div>
              <Btn disabled={loading}>{loading ? 'Setting up…' : 'Create my workspace'}</Btn>
            </form>
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
  googleBtn:   { width: '100%', padding: '11px', background: '#fff', color: COLORS.charcoal, border: `1px solid ${COLORS.border}`, borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  divider:     { display: 'flex', alignItems: 'center', textAlign: 'center', margin: '16px 0', color: COLORS.muted, fontSize: 12, fontFamily: 'system-ui' },
  dividerText: { background: COLORS.surface, padding: '0 10px', position: 'relative', margin: '0 auto', borderTop: `1px solid ${COLORS.border}`, top: 0 },
}
