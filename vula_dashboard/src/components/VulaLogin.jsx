/**
 * VulaLogin.jsx
 *
 * Handles these auth flows:
 *   0. Log in with WhatsApp — QR / button / 6-digit code (WhatsAppLogin.jsx, 15 Oct 2026)
 *   1. Normal sign-in (email + password)
 *   2. Password recovery — Supabase puts #access_token in URL after clicking
 *      the reset email; we detect this and show a "set new password" form
 *   3. First-time invite — same token flow as recovery
 *
 * Master user (Richard/Ian): awehbelekker@gmail.com
 * role = 'master'  → sees all tenants
 * role = 'owner'   → client, sees only their tenant
 */

import WhatsAppLogin from './WhatsAppLogin'
import { useLogin } from './login/useLogin'
import LoginV2 from './login/LoginV2'

const COLORS = {
  bg: 'var(--bg)',
  surface: 'var(--surface)',
  border: 'var(--border)',
  green: 'var(--accent)',
  amber: 'var(--warn)',
  muted: 'var(--muted)',
  charcoal: 'var(--ink)',
}

/** The redesign gets its own screen (login/LoginV2.jsx); the sign-in logic is shared (useLogin). */
export default function VulaLogin({ v2 = false, ...props }) {
  return v2 ? <LoginV2 {...props} /> : <LoginV1 {...props} />
}

function LoginV1({ onSuccess }) {
  const {
    mode, setMode, email, setEmail, password, setPassword, newPassword, setNewPassword,
    confirmPassword, setConfirmPassword, error, setError, info, setInfo, loading, loginBrand,
    pendingUser, businessName, setBusinessName, businessType, setBusinessType, businessTypes,
    resolveAndLogin, handleSignIn, handleSignUp, handleSetupWorkspace, handleGoogle,
    handleMagicLink, handleResetRequest, handleSetPassword,
  } = useLogin({ onSuccess })

  return (
    <div style={s.outer}>
      <div style={s.card}>
        <div style={s.logoWrap}>
          {loginBrand ? (
            <>
              {loginBrand.logo_url ? (
                <img src={loginBrand.logo_url} alt={loginBrand.name || ''} className="vula-login-logo"
                     style={{ marginBottom: 10 }} />
              ) : (
                <span style={{ ...s.logoText, color: 'var(--accent)' }}>{loginBrand.name}</span>
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
            <WhatsAppLogin onUser={(u) => resolveAndLogin(u).catch((e) => setError(e.message || 'Sign-in failed'))} styles={s} />
            <div style={s.divider}><span style={s.dividerText}>or with email</span></div>
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
  const activeStyle = { ...s.btn, background: 'var(--accent)', color: 'var(--on-accent)' }
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
  logoText:    { fontSize: 32, fontWeight: 700, color: COLORS.green},
  logoSub:     { fontSize: 12, color: COLORS.muted, letterSpacing: '0.08em', textTransform: 'uppercase'},
  heading:     { fontSize: 22, fontWeight: 700, color: COLORS.charcoal, margin: '0 0 4px'},
  sub:         { fontSize: 13, color: COLORS.muted, margin: '0 0 20px'},
  infoBox:     { background: 'var(--ok-soft)', border: '1px solid #86EFAC', color: 'var(--ok)', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 16 },
  errorBox:    { background: 'var(--danger-soft)', border: '1px solid var(--danger-soft)', color: 'var(--danger)', borderRadius: 6, padding: '10px 14px', fontSize: 13, marginBottom: 16 },
  form:        { display: 'flex', flexDirection: 'column', gap: 16 },
  label:       { fontSize: 13, fontWeight: 600, color: COLORS.charcoal},
  input:       { padding: '10px 12px', border: `1px solid ${COLORS.border}`, borderRadius: 6, fontSize: 14, color: COLORS.charcoal, background: COLORS.bg, outline: 'none'},
  btn:         { padding: '12px', background: COLORS.green, color: 'var(--on-accent)', border: 'none', borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: 'pointer'},
  btnDisabled: { padding: '12px', background: 'var(--border)', color: COLORS.muted, border: 'none', borderRadius: 6, fontSize: 14, cursor: 'not-allowed'},
  footer:      { marginTop: 20, textAlign: 'center' },
  textBtn:     { background: 'none', border: 'none', color: COLORS.green, fontSize: 13, cursor: 'pointer', textDecoration: 'underline' },
  googleBtn:   { width: '100%', padding: '11px', background: 'var(--surface)', color: COLORS.charcoal, border: `1px solid ${COLORS.border}`, borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  divider:     { display: 'flex', alignItems: 'center', textAlign: 'center', margin: '16px 0', color: COLORS.muted, fontSize: 12},
  dividerText: { background: COLORS.surface, padding: '0 10px', position: 'relative', margin: '0 auto', borderTop: `1px solid ${COLORS.border}`, top: 0 },
}
