/**
 * WhatsAppLogin.jsx — log in with WhatsApp (15 Oct 2026; backend vula_mind/vula/api/wa_login.py).
 *
 * On a business's own dashboard address the page shows a QR (computer) or an "Open WhatsApp"
 * button (phone). It opens WhatsApp with "Login K7F4QX" to the business's number; Vula asks the
 * person to Approve on WhatsApp and this page signs in. Anywhere else — or if they'd rather — they
 * type their WhatsApp number and get a 6-digit code. No password either way.
 */
import { isPhone, NO_CODE_HINT, useWhatsAppLogin } from './login/useWhatsAppLogin'

export default function WhatsAppLogin({ onUser, styles: s }) {
  const { qr, state, setState, msg, setMsg, err, setErr, phone, setPhone, code, setCode, busy, stop, startQr, sendCode, resend, verify } = useWhatsAppLogin({ onUser })

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
          <div style={{ fontSize: 12.5, textAlign: 'center' }}>{NO_CODE_HINT}</div>
        </form>
      )}

      {state === 'done' && <div style={{ fontSize: 13 }}>✅ Signing you in…</div>}

      <div style={{ fontSize: 12.5 }}>
        {(state === 'qr' || state === 'waiting') && link('Send me a code instead', () => { stop(); setErr(''); setMsg(''); setState('code_phone') })}
        {state === 'code_enter' && <>{link('Resend code', resend)}{' · '}</>}
        {state === 'code_enter' && link('Use a different number', () => { setCode(''); setState('code_phone') })}
        {(state === 'code_phone' || state === 'code_enter') && qr && <> {' · '}{link('Scan a QR code instead', startQr)}</>}
      </div>
    </div>
  )
}
