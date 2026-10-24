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
  const [state, setState] = useState('loading')   // loading | qr | waiting | code_phone | code_enter | done
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
            stop(); setState('done'); onUserRef.current(await signInWith(st.token_hash))
          } else if (st.status === 'denied') {
            stop(); setErr('That login was blocked on WhatsApp.'); setState('qr')
          } else if (st.status === 'expired' || st.status === 'used' || !r.ok) {
            stop(); setMsg('That code expired — here is a fresh one.'); startQr()
          }
        } catch (e) { stop(); setErr(e.message || 'Login failed — try again.') }
      }, 2000)
    } catch (e) { setErr(e.message); setState('code_phone') }
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
