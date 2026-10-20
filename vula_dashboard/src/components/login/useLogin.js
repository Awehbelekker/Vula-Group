/**
 * useLogin.js — every sign-in flow behind the login screen, shared by VulaLogin (current look)
 * and login/LoginV2 (the redesign, 2026-10-20). Moved verbatim out of VulaLogin.jsx: WhatsApp,
 * email + password, Google, magic link, password reset, invite/recovery set-password, and the
 * self-serve "set up your workspace" step for a signed-in user with no tenant yet.
 */
import { useState, useEffect } from 'react'
import { supabase } from '../../lib/supabase'
import { useAuthStore } from '../../store/auth'
import { resolveTenantFromHost } from '../../theme/tenantThemes'
import { applyBrand } from '../../theme/tokens'
import { VULA_API } from '../../lib/authFetch'

// Reached via a tenant's own dashboard address (admin.offthehook.co.za, gerflor.vula-ai.com…)?
// The backend resolves the host to a tenant (its domains / store / slug) and the login wears that
// tenant's saved brand — name, logo, colours, font. The static host list is only a first guess.
const HOST_HINT = resolveTenantFromHost()

export function useLogin({ onSuccess } = {}) {
  const [mode, setMode] = useState('login')      // 'login' | 'reset_request' | 'set_password' | 'signup' | 'setup_workspace'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [loading, setLoading] = useState(false)
  const [loginBrand, setLoginBrand] = useState(null)
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

  useEffect(() => {
    let alive = true
    const host = window.location.hostname
    fetch(`${VULA_API}/v1/tenants/by-domain?host=${encodeURIComponent(host)}`)
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .then((d) => {
        const tid = d?.tenant_id || HOST_HINT
        if (!tid) return null
        return fetch(`${VULA_API}/v1/commerce/${tid}/brand`).then((r) => (r.ok ? r.json() : null))
      })
      .then((b) => { if (alive && b) { applyBrand(b); setLoginBrand(b) } })
      .catch(() => {})
    return () => { alive = false }
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

  return {
    mode, setMode, email, setEmail, password, setPassword, newPassword, setNewPassword,
    confirmPassword, setConfirmPassword, error, setError, info, setInfo, loading, loginBrand,
    pendingUser, businessName, setBusinessName, businessType, setBusinessType, businessTypes,
    resolveAndLogin, handleSignIn, handleSignUp, handleSetupWorkspace, handleGoogle,
    handleMagicLink, handleResetRequest, handleSetPassword,
  }
}
