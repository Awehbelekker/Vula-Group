/**
 * LoginV2.jsx — the redesigned sign-in screen (2026-10-20, UX redesign Phase F). Same flows as
 * VulaLogin (login/useLogin.js): WhatsApp first, then Google, then email + password, plus
 * magic link, password reset, invite/recovery set-password and the self-serve workspace step.
 * Wears the tenant's brand on its own dashboard address (logo or name, "Powered by Vula").
 */
import { useState } from 'react'
import { ArrowLeft, Building2, Eye, EyeOff, KeyRound, LogIn, Mail, MessageCircle, QrCode, RotateCcw, Smartphone, UserPlus } from 'lucide-react'
import { Button, Card, Field, IconButton, Input, Select } from '../ui/index.jsx'
import VulaLogo, { PoweredByVula } from '../brand/VulaLogo.jsx'
import { useLogin } from './useLogin'
import { isPhone, useWhatsAppLogin } from './useWhatsAppLogin'
import './login.css'

export default function LoginV2({ onSuccess }) {
  const x = useLogin({ onSuccess })
  const go = (mode) => { x.setMode(mode); x.setError(''); x.setInfo('') }
  const b = x.loginBrand

  return (
    <main className="vlg">
      <Card className="vlg-card" pad={0}>
        <header className="vlg-brand">
          {b ? (
            <>
              {b.logo_url ? <img src={b.logo_url} alt={b.name || ''} className="vlg-logo vula-login-logo" />
                : <span className="vlg-name">{b.name}</span>}
              <PoweredByVula />
            </>
          ) : (
            <>
              <VulaLogo kind="lockup" height={48} title="Vula" />
              <span className="vlg-over">Business dashboard</span>
            </>
          )}
        </header>

        {x.mode === 'login' && (
          <>
            <h1 className="vlg-h1">Sign in</h1>
            <Notes x={x} />
            <WhatsAppPanel onUser={(u) => x.resolveAndLogin(u).catch((e) => x.setError(e.message || 'Sign-in failed'))} />
            <Divider>or</Divider>
            <Button variant="secondary" size="lg" className="vlg-wide" type="button" onClick={x.handleGoogle}>Continue with Google</Button>
            <Divider>or with email</Divider>
            <form onSubmit={x.handleSignIn} className="vlg-form">
              <Email x={x} />
              <Field label="Password">
                <Password required value={x.password} onChange={(e) => x.setPassword(e.target.value)} autoComplete="current-password" />
              </Field>
              <Button size="lg" Icon={LogIn} disabled={x.loading} className="vlg-wide">{x.loading ? 'Signing in…' : 'Sign in'}</Button>
            </form>
            <p className="vlg-links">
              <Button variant="quiet" size="sm" type="button" Icon={Mail} onClick={x.handleMagicLink}>Email me a link</Button>
              <Button variant="quiet" size="sm" type="button" Icon={KeyRound} onClick={() => go('reset_request')}>Forgot password?</Button>
            </p>
            <p className="vlg-foot">New here? <Button variant="quiet" size="sm" type="button" Icon={UserPlus} onClick={() => go('signup')}>Create an account</Button></p>
          </>
        )}

        {x.mode === 'signup' && (
          <>
            <h1 className="vlg-h1">Create your account</h1>
            <p className="vlg-sub">Next you'll set up your business workspace.</p>
            <Notes x={x} info={false} />
            <Button variant="secondary" size="lg" className="vlg-wide" type="button" onClick={x.handleGoogle}>Continue with Google</Button>
            <Divider>or</Divider>
            <form onSubmit={x.handleSignUp} className="vlg-form">
              <Email x={x} />
              <Field label="Password" hint="At least 8 characters">
                <Password required value={x.password} onChange={(e) => x.setPassword(e.target.value)} autoComplete="new-password" />
              </Field>
              <Button size="lg" Icon={UserPlus} disabled={x.loading} className="vlg-wide">{x.loading ? 'Creating account…' : 'Create account'}</Button>
            </form>
            <Back onClick={() => { x.setMode('login'); x.setError('') }} />
          </>
        )}

        {x.mode === 'setup_workspace' && (
          <>
            <h1 className="vlg-h1">Set up your workspace</h1>
            <p className="vlg-sub">Signed in as {x.pendingUser?.email}. What's your business called?</p>
            <Notes x={x} info={false} />
            <form onSubmit={x.handleSetupWorkspace} className="vlg-form">
              <Field label="Business name">
                <Input type="text" required value={x.businessName} onChange={(e) => x.setBusinessName(e.target.value)} placeholder="e.g. Off the Hook" />
              </Field>
              <Field label="What kind of business?">
                <Select value={x.businessType} onChange={(e) => x.setBusinessType(e.target.value)}>
                  {(x.businessTypes.length ? x.businessTypes : [{ id: 'other', label: 'Other / General' }])
                    .map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                </Select>
              </Field>
              <Button size="lg" Icon={Building2} disabled={x.loading} className="vlg-wide">{x.loading ? 'Setting up…' : 'Create my workspace'}</Button>
            </form>
          </>
        )}

        {x.mode === 'reset_request' && (
          <>
            <h1 className="vlg-h1">Reset password</h1>
            <p className="vlg-sub">We'll email you a link to set a new password.</p>
            <Notes x={x} info={false} />
            <form onSubmit={x.handleResetRequest} className="vlg-form">
              <Email x={x} />
              <Button size="lg" Icon={Mail} disabled={x.loading} className="vlg-wide">{x.loading ? 'Sending…' : 'Send reset email'}</Button>
            </form>
            <Back onClick={() => { x.setMode('login'); x.setError('') }} />
          </>
        )}

        {x.mode === 'set_password' && (
          <>
            <h1 className="vlg-h1">Set your password</h1>
            <Notes x={x} />
            <form onSubmit={x.handleSetPassword} className="vlg-form">
              <Field label="New password" hint="At least 8 characters">
                <Password required value={x.newPassword} onChange={(e) => x.setNewPassword(e.target.value)} autoComplete="new-password" />
              </Field>
              <Field label="Confirm password">
                <Password required value={x.confirmPassword} onChange={(e) => x.setConfirmPassword(e.target.value)} autoComplete="new-password" />
              </Field>
              <Button size="lg" Icon={KeyRound} disabled={x.loading} className="vlg-wide">{x.loading ? 'Saving…' : 'Set password & sign in'}</Button>
            </form>
          </>
        )}
      </Card>
    </main>
  )
}

function Notes({ x, info = true }) {
  return (
    <>
      {info && x.info && <p className="vlg-info" role="status">{x.info}</p>}
      {x.error && <p className="vlg-error" role="alert">{x.error}</p>}
    </>
  )
}

function Email({ x }) {
  return (
    <Field label="Email">
      <Input type="email" required value={x.email} onChange={(e) => x.setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" />
    </Field>
  )
}

/** A password box with a show/hide eye, so a typo can be checked before signing in. */
function Password(props) {
  const [shown, setShown] = useState(false)
  return (
    <span className="vlg-pw">
      <Input {...props} type={shown ? 'text' : 'password'} />
      <IconButton quiet Icon={shown ? EyeOff : Eye} label={shown ? 'Hide password' : 'Show password'}
        aria-pressed={shown} className="vlg-pw-eye" onClick={(e) => { e.preventDefault(); setShown(!shown) }} />
    </span>
  )
}

const Divider = ({ children }) => <p className="vlg-divider"><span>{children}</span></p>
const Back = ({ onClick }) => (
  <p className="vlg-foot"><Button variant="quiet" size="sm" type="button" Icon={ArrowLeft} onClick={onClick}>Back to sign in</Button></p>
)

/** Log in with WhatsApp: a QR on a computer, an "Open WhatsApp" link on a phone, or a 6-digit code. */
function WhatsAppPanel({ onUser }) {
  const w = useWhatsAppLogin({ onUser })
  const scanning = (w.state === 'qr' || w.state === 'waiting') && w.qr
  return (
    <section className="vlg-wa" aria-label="Log in with WhatsApp">
      <h2 className="vlg-h2"><MessageCircle size={18} aria-hidden="true" />Log in with WhatsApp</h2>
      {w.err && <p className="vlg-error" role="alert">{w.err}</p>}
      {w.msg && !w.err && <p className="vlg-muted" role="status">{w.msg}</p>}
      {w.state === 'loading' && <p className="vlg-muted" role="status">Getting your code…</p>}

      {scanning && (isPhone() ? (
        <>
          <a href={w.qr.link} className="vlg-wa-open"><MessageCircle size={18} aria-hidden="true" />Open WhatsApp</a>
          <p className="vlg-muted" role="status">
            {w.state === 'waiting' ? 'Now tap Approve in WhatsApp…' : <>Tap Send on the message (<b>Login {w.qr.code}</b>), then Approve.</>}
          </p>
        </>
      ) : (
        <>
          <div aria-label="WhatsApp login QR code" role="img" className="vlg-qr" dangerouslySetInnerHTML={{ __html: w.qr.qr_svg }} />
          <p className="vlg-muted" role="status">
            {w.state === 'waiting'
              ? 'Now tap Approve in WhatsApp on your phone…'
              : <>Scan with your phone's camera, tap Send in WhatsApp (<b>Login {w.qr.code}</b>), then Approve.</>}
          </p>
        </>
      ))}

      {w.state === 'code_phone' && (
        <form onSubmit={w.sendCode} className="vlg-form vlg-wide">
          <Field label="Your WhatsApp number">
            <Input type="tel" required value={w.phone} onChange={(e) => w.setPhone(e.target.value)} placeholder="e.g. 082 123 4567" autoComplete="tel" />
          </Field>
          <Button size="lg" Icon={Smartphone} disabled={w.busy} className="vlg-wide">{w.busy ? 'Sending…' : 'Send me a code'}</Button>
        </form>
      )}

      {w.state === 'code_enter' && (
        <form onSubmit={w.verify} className="vlg-form vlg-wide">
          <Field label="6-digit code">
            <Input inputMode="numeric" required maxLength={6} value={w.code} className="vlg-code"
              onChange={(e) => w.setCode(e.target.value.replace(/\D/g, ''))} autoComplete="one-time-code" />
          </Field>
          <Button size="lg" Icon={LogIn} disabled={w.busy || w.code.length !== 6} className="vlg-wide">{w.busy ? 'Checking…' : 'Log in'}</Button>
        </form>
      )}

      {w.state === 'done' && <p className="vlg-ok" role="status">Signing you in…</p>}

      <p className="vlg-links">
        {scanning && <Button variant="quiet" size="sm" type="button" Icon={Smartphone}
          onClick={() => { w.stop(); w.setErr(''); w.setMsg(''); w.setState('code_phone') }}>Send me a code instead</Button>}
        {w.state === 'code_enter' && <Button variant="quiet" size="sm" type="button" Icon={RotateCcw}
          onClick={() => { w.setCode(''); w.setState('code_phone') }}>Use a different number</Button>}
        {(w.state === 'code_phone' || w.state === 'code_enter') && w.qr && <Button variant="quiet" size="sm" type="button" Icon={QrCode}
          onClick={w.startQr}>Scan a QR code instead</Button>}
      </p>
    </section>
  )
}
