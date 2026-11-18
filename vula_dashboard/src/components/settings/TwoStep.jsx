/**
 * TwoStep.jsx — Settings › Two-step sign-in (18 Nov 2026). Turn on an authenticator app for the
 * signed-in login (lib/mfa.js): scan the QR code, type the first code, done. From then on every
 * sign-in — password, Google, email link or WhatsApp — asks for the app's code too, and Vula's
 * server refuses a session without it. Turning it off needs a session that gave the code.
 */
import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, ShieldOff } from 'lucide-react'
import { Button, Card, ErrorCard, Field, Input, Skeleton, StatusChip, confirmDialog, toast } from '../ui/index.jsx'
import { TwoStepSetupSteps } from '../login/LoginV2.jsx'
import { finishSetup, startSetup, turnOff, verifiedFactor } from '../../lib/mfa'
import '../login/login.css'
import './settings.css'

export default function TwoStep() {
  const [on, setOn] = useState(null)          // null = loading
  const [failed, setFailed] = useState(false)
  const [setup, setSetup] = useState(null)    // { factorId, qr, secret } while setting up
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const reload = useCallback(() => {
    setFailed(false)
    verifiedFactor().then((f) => setOn(Boolean(f))).catch(() => setFailed(true))
  }, [])
  useEffect(() => { reload() }, [reload])

  async function begin() {
    setErr(''); setBusy(true)
    try { setSetup(await startSetup()) } catch (e) { setErr(e.message || "Couldn't start the set-up. Try again.") }
    finally { setBusy(false) }
  }

  async function confirm(e) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      await finishSetup(setup.factorId, code)
      setSetup(null); setCode(''); setOn(true)
      toast('Two-step sign-in is on. Keep the app on your phone — you need it every time you sign in.')
    } catch (e2) {
      setErr(e2.message || "That code didn't work."); setCode('')
    } finally { setBusy(false) }
  }

  async function off() {
    if (!(await confirmDialog('Turn off two-step sign-in? Anyone with your password could then sign in as you.',
      { confirmLabel: 'Turn off', danger: true }))) return
    setBusy(true)
    try { await turnOff(); setOn(false); toast('Two-step sign-in is off.') }
    catch (e) { toast(e.message || "That didn't go through. Sign out and in again with your code, then try again.", 'danger') }
    finally { setBusy(false) }
  }

  if (failed) return <ErrorCard what="your sign-in settings" onRetry={reload} />
  if (on === null) return <Card><Skeleton lines={3} /></Card>
  return (
    <Card>
      <div className="vst-card">
        <p><StatusChip tone={on ? 'ok' : 'warn'}>{on ? 'On for your login' : 'Off'}</StatusChip></p>
        <p className="vst-body">
          With two-step sign-in, signing in to Vula needs your password (or Google, an email link or WhatsApp) and a
          6-digit code from an app on your phone. Someone who learns your password still can't get in.
          It's for your own login only; everyone in your team turns it on for themselves.
        </p>
        {err && <p className="vlg-error" role="alert">{err}</p>}
        {on && <Button variant="secondary" Icon={ShieldOff} disabled={busy} onClick={off}>Turn off</Button>}
        {!on && !setup && <Button Icon={ShieldCheck} disabled={busy} onClick={begin}>{busy ? 'Starting…' : 'Turn on'}</Button>}
        {!on && setup && (
          <>
            <TwoStepSetupSteps setup={setup} />
            <form onSubmit={confirm} className="vlg-form">
              <Field label="6-digit code">
                <Input inputMode="numeric" required maxLength={6} value={code} autoComplete="one-time-code"
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
              </Field>
              <Button Icon={ShieldCheck} disabled={busy || code.length !== 6}>{busy ? 'Checking…' : 'Turn on'}</Button>
            </form>
          </>
        )}
      </div>
    </Card>
  )
}
