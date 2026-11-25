/**
 * useLogin.js — every sign-in flow behind the login screen, shared by VulaLogin (current look)
 * and login/LoginV2 (the redesign, 2026-10-20). Moved verbatim out of VulaLogin.jsx: WhatsApp,
 * email + password, Google, magic link, password reset, invite/recovery set-password, and the
 * self-serve "set up your workspace" step for a signed-in user with no tenant yet.
 */
import { pickAssignment } from '../../lib/assignment'
import { useState, useEffect, useRef } from 'react'
import { supabase, supabaseConfigured } from '../../lib/supabase'
import { finishSetup, giveCode, startSetup, twoStepNeeded } from '../../lib/mfa'
import { useAuthStore } from '../../store/auth'
import { resolveTenantFromHost } from '../../theme/tenantThemes'
import { applyBrand } from '../../theme/tokens'
import { VULA_API } from '../../lib/authFetch'

// Reached via a tenant's own dashboard address (admin.offthehook.co.za, gerflor.vula-ai.com…)?
// The backend resolves the host to a tenant (its domains / store / slug) and the login wears that
// tenant's saved brand — name, logo, colours, font. The static host list is only a first guess.
const HOST_HINT = resolveTenantFromHost()

export function useLogin({ onSuccess, notice = '', email: knownEmail = '' } = {}) {
  const [mode, setMode] = useState('login')      // 'login' | 'reset_request' | 'set_password' | 'signup' | 'setup_workspace' | 'two_step_code' | 'two_step_setup'
  const [email, setEmail] = useState(knownEmail)   // who was signed in, when the sign-in ran out
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState(notice)       // e.g. "You've been signed out on this device…"
  const [loading, setLoading] = useState(false)
  const [loginBrand, setLoginBrand] = useState(null)
  const { login } = useAuthStore()

  // Self-serve workspace setup (2026-08-15) — a signed-in Supabase user with no
  // vula_tenant_users row lands here instead of the old dead-end error, via resolveAndLogin below.
  const [pendingUser, setPendingUser] = useState(null)
  const [businessName, setBusinessName] = useState('')
  const [businessType, setBusinessType] = useState('other')
  const [businessTypes, setBusinessTypes] = useState([])

  // Two-step sign-in (18 Nov 2026, lib/mfa.js): after the password / Google / WhatsApp step, a
  // login with an authenticator app gives its 6-digit code here; a role that must have one sets
  // it up here (QR code). Vula's server refuses the session until then.
  const [twoStepCode, setTwoStepCode] = useState('')
  const [twoStepSetup, setTwoStepSetup] = useState(null)    // { factorId, qr, secret }
  const pendingEntry = useRef(null)                          // { user, assignment, needed } waiting on it
  const entered = useRef(false)
  const setupStarted = useRef(false)

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
        if (d?.partner) {                                     // a partner's own address (flowcrew.co.za): its name and look
          document.title = d.partner.name
          return { ...d.partner.brand, name: d.partner.name, logo_url: `/partners/${d.partner.id}-mark.png`, partner_login: true }
        }
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
    const { data: rows, error: dbErr } = await supabase
      .from('vula_tenant_users')
      .select('tenant_id, role')
      .eq('user_id', user.id)
      .limit(50)
    const assignment = pickAssignment(rows || [])
    if (dbErr || !assignment) {
      setPendingUser(user)
      setBusinessName('')
      setError('')
      setMode('setup_workspace')
      return
    }
    const needed = await twoStepNeeded(assignment.role)
    if (needed) {
      // the auth listener fires again (token refresh…): don't wipe a code being typed
      if (pendingEntry.current?.needed === needed) return
      pendingEntry.current = { user, assignment, needed }
      setPendingUser(user)
      setError('')
      setTwoStepCode('')
      if (needed === 'code') {
        setInfo('')
        setMode('two_step_code')
      } else {
        setInfo('Your role needs two-step sign-in. Set it up once with an authenticator app — it takes a minute.')
        setMode('two_step_setup')
        if (!setupStarted.current) {
          setupStarted.current = true
          try { setTwoStepSetup(await startSetup()) } catch (err) {
            setupStarted.current = false
            setError(err.message || "Couldn't start the set-up. Try again.")
          }
        }
      }
      return
    }
    enter(user, assignment)
  }

  function enter(user, assignment) {
    if (entered.current) return            // the auth listener and the form can both get here
    entered.current = true
    login(
      { id: user.id, email: user.email, name: user.user_metadata?.full_name || (user.email || '').split('@')[0] },
      assignment.tenant_id,
      assignment.role,
    )
    onSuccess?.()
  }

  // ── Two-step sign-in: the code from the app ─────────────────────────────────
  async function handleTwoStepCode(e) {
    e?.preventDefault?.()
    setError(''); setLoading(true)
    try {
      await giveCode(twoStepCode)
      const p = pendingEntry.current
      if (p) enter(p.user, p.assignment)
    } catch (err) {
      setError(err.message || "That code didn't work.")
      setTwoStepCode('')
    } finally {
      setLoading(false)
    }
  }

  // ── Two-step sign-in: first set-up (scan the QR, type the first code) ────────
  async function handleTwoStepSetup(e) {
    e?.preventDefault?.()
    if (!twoStepSetup) return
    setError(''); setLoading(true)
    try {
      await finishSetup(twoStepSetup.factorId, twoStepCode)
      const p = pendingEntry.current
      if (p) enter(p.user, p.assignment)
    } catch (err) {
      setError(err.message || "That code didn't work.")
      setTwoStepCode('')
    } finally {
      setLoading(false)
    }
  }

  async function cancelTwoStep() {
    pendingEntry.current = null
    setupStarted.current = false
    setTwoStepSetup(null); setTwoStepCode(''); setError(''); setInfo('')
    await supabase.auth.signOut().catch(() => {})
    setMode('login')
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
    if (!supabaseConfigured) {
      setError("Sign-in isn't set up on this copy of the app. Tell the person who runs it.")
      return
    }
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
      const { data: rows, error: dbErr } = await supabase
        .from('vula_tenant_users')
        .select('tenant_id, role')
        .eq('user_id', user.id)
        .limit(50)
      const assignment = pickAssignment(rows || [])

      if (dbErr || !assignment) {
        throw new Error('Password set! Now sign in with your new password.')
      }

      await resolveAndLogin(user)
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

  // a partner's address: someone who signed in to book lessons, not to run a school — on to the
  // customer app, which reads the sign-in once from the address's #fragment (never sent to a server)
  const toLessons = async () => {
    const { data } = await supabase.auth.getSession()
    const t = data?.session
    window.location.assign('/flow/find' + (t ? `#access_token=${t.access_token}&refresh_token=${t.refresh_token}&expires_in=${t.expires_in}` : ''))
  }

  return {
    mode, setMode, email, setEmail, password, setPassword, newPassword, setNewPassword,
    confirmPassword, setConfirmPassword, error, setError, info, setInfo, loading, loginBrand,
    pendingUser, toLessons, businessName, setBusinessName, businessType, setBusinessType, businessTypes,
    resolveAndLogin, handleSignIn, handleSignUp, handleSetupWorkspace, handleGoogle,
    handleMagicLink, handleResetRequest, handleSetPassword,
    twoStepCode, setTwoStepCode, twoStepSetup, handleTwoStepCode, handleTwoStepSetup, cancelTwoStep,
  }
}
