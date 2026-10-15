/**
 * WhatsAppLogin.jsx — log in with WhatsApp (15 Oct 2026; backend vula_mind/vula/api/wa_login.py).
 *
 * On a business's own dashboard address the page shows a QR (computer) or an "Open WhatsApp"
 * button (phone). It opens WhatsApp with "Login K7F4QX" to the business's number; Vula asks the
 * person to Approve on WhatsApp and this page signs in. Anywhere else — or if they'd rather — they
 * type their WhatsApp number and get a 6-digit code. No password either way.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from '../lib/supabase'
import { VULA_API } from '../lib/authFetch'

const isPhone = () => /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || '')

async function post(path, body) {
  const r = await fetch(`${VULA_API}/v1/auth/wa${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || 'Something went wrong — try again.')
  return d
}

async function signInWith(tokenHash) {
  const { data, error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type: 'email' })
  if (error) throw error
  return data.user
}

export default function WhatsAppLogin({ onUser, styles: s }) {
  const [qr, setQr] = useState(null)              // {id, secret, link, qr_svg, code}
  const [state, setState] = useState('loading')   // loading | qr | waiting | code_phone | code_enter | done
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [codeReq, setCodeReq] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)

  const stop = () => { if (timer.current) { clearInterval(timer.current); timer.current = null } }

  const startQr = useCallback(async () => {
    stop(); setErr(''); setMsg('')
    try {
      const d = await post('/start', { host: window.location.hostname })
      if (!d.available) { setState('code_phone'); return }
      setQr(d); setState('qr')
      timer.current = setInterval(async () => {
        try {
          const r = await fetch(`${VULA_API}/v1/auth/wa/status?id=${encodeURIComponent(d.id)}&secret=${encodeURIComponent(d.secret)}`)
          const st = await r.json().catch(() => ({}))
          if (st.status === 'awaiting_approval') { setState('waiting') }
          else if (st.status === 'approved' && st.token_hash) {
            stop(); setState('done'); onUser(await signInWith(st.token_hash))
          } else if (st.status === 'denied') {
            stop(); setErr('That login was blocked on WhatsApp.'); setState('qr')
          } else if (st.status === 'expired' || st.status === 'used' || !r.ok) {
            stop(); setMsg('That code expired — here is a fresh one.'); startQr()
          }
        } catch (e) { stop(); setErr(e.message || 'Login failed — try again.') }
      }, 2000)
    } catch (e) { setErr(e.message); setState('code_phone') }
  }, [onUser])

  useEffect(() => { startQr(); return stop }, [startQr])

  async function sendCode(e) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const d = await post('/code', { phone, host: window.location.hostname })
      setCodeReq(d); setMsg(d.message); setState('code_enter')
    } catch (e2) { setErr(e2.message) }
    setBusy(false)
  }

  async function verify(e) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const d = await post('/code/verify', { id: codeReq.id, secret: codeReq.secret, code })
      setState('done'); onUser(await signInWith(d.token_hash))
    } catch (e2) { setErr(e2.message) }
    setBusy(false)
  }

  const box = { border: '1px solid var(--border)', borderRadius: 14, padding: 16, display: 'flex',
                flexDirection: 'column', alignItems: 'center', gap: 10, textAlign: 'center' }
  const link = (label, fn) => <button type="button" style={s.textBtn} onClick={fn}>{label}</button>

  return (
    <div style={box}>
      <div style={{ fontWeight: 700, fontSize: 15 }}>💬 Log in with WhatsApp</div>
      {err && <div style={{ ...s.errorBox, width: '100%' }}>{err}</div>}
      {msg && !err && <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{msg}</div>}

      {state === 'loading' && <div style={{ fontSize: 13, color: 'var(--muted)' }}>Getting your code…</div>}

      {(state === 'qr' || state === 'waiting') && qr && (isPhone() ? (
        <>
          <a href={qr.link} style={{ ...s.btn, textDecoration: 'none', display: 'block', width: '100%',
                                    textAlign: 'center', background: 'var(--accent)', color: 'var(--on-accent)' }}>Open WhatsApp</a>
          <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>
            {state === 'waiting' ? 'Now tap Approve in WhatsApp…' : <>Tap Send on the message (<b>Login {qr.code}</b>), then Approve.</>}
          </div>
        </>
      ) : (
        <>
          <div aria-label="WhatsApp login QR code" style={{ width: 196, height: 196, background: 'white', borderRadius: 10, padding: 6 }}
               dangerouslySetInnerHTML={{ __html: qr.qr_svg }} />
          <div style={{ fontSize: 12.5, color: 'var(--muted)', maxWidth: 280 }}>
            {state === 'waiting'
              ? 'Now tap Approve in WhatsApp on your phone…'
              : <>Scan with your phone's camera, tap Send in WhatsApp (<b>Login {qr.code}</b>), then Approve.</>}
          </div>
        </>
      ))}

      {state === 'code_phone' && (
        <form onSubmit={sendCode} style={{ ...s.form, width: '100%' }}>
          <input type="tel" required value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Your WhatsApp number, e.g. 082 123 4567"
                 style={s.input} autoComplete="tel" />
          <button disabled={busy} style={s.btn}>{busy ? 'Sending…' : 'Send me a code'}</button>
        </form>
      )}

      {state === 'code_enter' && (
        <form onSubmit={verify} style={{ ...s.form, width: '100%' }}>
          <input inputMode="numeric" required maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                 placeholder="6-digit code" style={{ ...s.input, textAlign: 'center', letterSpacing: 6, fontSize: 20 }}
                 autoComplete="one-time-code" />
          <button disabled={busy || code.length !== 6} style={s.btn}>{busy ? 'Checking…' : 'Log in'}</button>
        </form>
      )}

      {state === 'done' && <div style={{ fontSize: 13 }}>✅ Signing you in…</div>}

      <div style={{ fontSize: 12.5 }}>
        {(state === 'qr' || state === 'waiting') && link('Send me a code instead', () => { stop(); setErr(''); setMsg(''); setState('code_phone') })}
        {state === 'code_enter' && link('Use a different number', () => { setCode(''); setState('code_phone') })}
        {(state === 'code_phone' || state === 'code_enter') && qr && <> {' · '}{link('Scan a QR code instead', startQr)}</>}
      </div>
    </div>
  )
}
