/**
 * useWhatsAppLogin.js — "Log in with WhatsApp" (backend vula_mind/vula/api/wa_login.py), shared by
 * WhatsAppLogin (current look) and login/LoginV2 (the redesign, 2026-10-20). Moved verbatim out
 * of WhatsAppLogin.jsx: a QR / "Open WhatsApp" link to the business's number polled until the
 * person taps Approve, or a 6-digit code sent to their WhatsApp number. No password either way.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from '../../lib/supabase'
import { VULA_API } from '../../lib/authFetch'

export const NO_CODE_HINT = "No code after a minute? Send “hi” to your business's WhatsApp number, then tap Resend code."

const POLL_MS = 2000
const MAX_POLL_MS = 30000
const MAX_FRESH = 3

export const isPhone = () => /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || '')

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

export function useWhatsAppLogin({ onUser }) {
  const [qr, setQr] = useState(null)              // {id, secret, link, qr_svg, code}
  const [state, setState] = useState('loading')   // loading | qr | waiting | expired | code_phone | code_enter | done
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [codeReq, setCodeReq] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)
  // The caller's onUser is a fresh arrow on every render; reading it through a ref keeps startQr
  // stable, so typing elsewhere on the login page no longer throws away the QR and starts a new
  // WhatsApp login session on each keystroke (2026-10-20).
  const onUserRef = useRef(onUser)
  onUserRef.current = onUser

  // Each QR session gets a number; stop() moves it on, so an answer that arrives after the person
  // switched to "Send me a code instead" (a status check already in flight, or the first of
  // dev's two StrictMode starts) is dropped instead of flipping the page back to the QR (5 Nov 2026).
  const session = useRef(0)
  const stop = () => { session.current += 1; if (timer.current) { clearTimeout(timer.current); timer.current = null } }
  // Fresh QRs made without a tap, since the page opened or the person last asked for one.
  const fresh = useRef(0)

  // Checking the QR (5 Nov 2026, after a sign-in page asked for hundreds of QRs and the server
  // refused it with 429s): one check at a time, the next only after the last answered; a busy or
  // failing server (429, 5xx, offline) is waited out with longer gaps on the SAME QR; a new QR
  // only when the server says this one is expired, used or gone (404), and at most MAX_FRESH of
  // those in a row — then the page offers "Show a new QR code" instead of making more on its own.
  const startQr = useCallback(async ({ note = '', byHand = true } = {}) => {
    stop(); setErr(''); setMsg(note)
    if (byHand) fresh.current = 0
    const mine = session.current
    const live = () => mine === session.current
    const expired = () => {
      stop()
      if (fresh.current >= MAX_FRESH) { setMsg('That code expired.'); setState('expired'); return }
      fresh.current += 1
      startQr({ note: 'That code expired — here is a fresh one.', byHand: false })
    }
    try {
      const d = await post('/start', { host: window.location.hostname })
      if (!live()) return
      if (!d.available) { setState('code_phone'); return }
      setQr(d); setState('qr')
      let wait = POLL_MS
      const check = async () => {
        let r = null, st = {}
        try {
          r = await fetch(`${VULA_API}/v1/auth/wa/status?id=${encodeURIComponent(d.id)}&secret=${encodeURIComponent(d.secret)}`)
          st = await r.json().catch(() => ({}))
        } catch { r = null }                                      // offline or the server didn't answer
        if (!live()) return
        if (r?.ok) {
          wait = POLL_MS
          if (st.status === 'awaiting_approval') setState('waiting')
          else if (st.status === 'approved' && st.token_hash) {
            stop(); setState('done')
            try { onUserRef.current(await signInWith(st.token_hash)) } catch (e) { setErr(e.message || 'Login failed — try again.') }
            return
          } else if (st.status === 'denied') { stop(); setErr('That login was blocked on WhatsApp.'); setState('qr'); return }
          else if (st.status === 'expired' || st.status === 'used') { expired(); return }
        } else if (r?.status === 404) { expired(); return }       // the server no longer knows this QR
        else wait = Math.min(wait * 2, MAX_POLL_MS)               // busy, rate-limited or offline: wait longer
        timer.current = setTimeout(check, wait)
      }
      timer.current = setTimeout(check, wait)
    } catch (e) { if (live()) { setErr(e.message); setState('code_phone') } }
  }, [])

  useEffect(() => { startQr(); return stop }, [startQr])

  async function sendCode(e) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const d = await post('/code', { phone, host: window.location.hostname })
      setCodeReq(d); setMsg(d.message); setState('code_enter')
    } catch (e2) { setErr(e2.message) }
    setBusy(false)
  }

  // No code? A text only reaches them on a number they messaged in the last 24 hours, so
  // "send hi, then resend" opens that window (vula_mind/vula/api/wa_login.py::_deliver_code).
  async function resend() {
    setErr(''); setCode(''); setBusy(true)
    try {
      const d = await post('/code', { phone, host: window.location.hostname })
      setCodeReq(d); setMsg('A new code is on its way.')
    } catch (e2) { setErr(e2.message) }
    setBusy(false)
  }

  async function verify(e) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const d = await post('/code/verify', { id: codeReq.id, secret: codeReq.secret, code })
      setState('done'); onUserRef.current(await signInWith(d.token_hash))
    } catch (e2) { setErr(e2.message) }
    setBusy(false)
  }

  return { qr, state, setState, msg, setMsg, err, setErr, phone, setPhone, code, setCode, codeReq, busy, stop, startQr, sendCode, resend, verify }
}
