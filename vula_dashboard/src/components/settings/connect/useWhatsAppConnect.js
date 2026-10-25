/**
 * useWhatsAppConnect.js — connect a WhatsApp Business number through Meta Embedded Signup
 * (2026-10-19, UX redesign Phase F). Moved out of VulaWhatsAppConnect unchanged: loads the
 * Facebook SDK, takes the WABA + number the owner picked from the signup's window message, sends
 * the code to POST /v1/whatsapp/connect; status from /v1/whatsapp/connect/status, disconnect via
 * DELETE /v1/whatsapp/disconnect.
 *
 * 2026-10-25: a business can have several numbers, each with a purpose (status's `numbers`):
 * change one's purpose or make it the main number (PATCH /v1/whatsapp/numbers), disconnect one,
 * or add another — Connect then sends the purpose chosen for it.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog } from '../../ui/index.jsx'

export const FB_APP_ID = import.meta.env.VITE_FB_APP_ID || ''
// Meta's Embedded Signup configuration. Without it FB.login opens nothing useful and the button
// used to hang on "Connecting…" — so Connect stays off and the card says why (2026-10-24).
export const FB_CONFIG_ID = import.meta.env.VITE_FB_CONFIG_ID || ''

// What a number can be for (vula_mind/vula/whatsapp_numbers.py).
export const PURPOSES = [
  ['shop', 'Customers: shop and orders'],
  ['bookings', 'Customers: bookings'],
  ['assistant', 'Assistant: answers from your documents'],
  ['team', 'Team only'],
]
export const PURPOSE_HINTS = {
  shop: 'Customers browse, order and pay here.',
  bookings: 'Customers book, move and cancel appointments here first.',
  assistant: 'Answers questions from your documents and price lists.',
  team: 'Only your team is answered here; customers are pointed to your main number.',
}

/** The status payload's numbers; an older backend sends only the one number at the top level. */
export function numbersOf(account) {
  if (!account) return []
  if (Array.isArray(account.numbers)) return account.numbers
  return account.phone_number_id || account.phone_number ? [{ ...account, is_primary: true,
    purpose: account.purpose || 'shop' }] : []
}

export function useWhatsAppConnect({ tenantId, tenantName, adminEmail }) {
  const [status, setStatus] = useState(null)   // null | 'connected' | 'pending' | 'error'
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [health, setHealth] = useState(null)   // { rows: [{label, value, tone}], error? }

  const [busyNumber, setBusyNumber] = useState(null)
  const [notice, setNotice] = useState(null)   // what a move to the owner's own Meta account did

  // Load current connection status
  const reload = useCallback(() => {
    if (!tenantId) return Promise.resolve()
    return fetch(`${VULA_API}/v1/whatsapp/connect/status/${tenantId}`)
      .then(r => r.json())
      .then(data => {
        setStatus(data.status)
        setAccount(data)
      })
      .catch(() => setStatus('error'))
  }, [tenantId])
  useEffect(() => { reload() }, [reload])

  // What Meta says about the connected number: quality, daily limit, display name, registered.
  useEffect(() => {
    if (!tenantId || status !== 'connected') { setHealth(null); return }
    fetch(`${VULA_API}/v1/whatsapp/health/${tenantId}`)
      .then(r => r.json())
      .then(setHealth)
      .catch(() => setHealth({ rows: [], error: "Couldn't read the number's health just now." }))
  }, [tenantId, status])

  // Embedded Signup posts the WhatsApp account + number the owner picked as a window message.
  // Sending those to the backend connects THAT number — before, the first number found on any of
  // the owner's businesses was used. A WhatsApp Business app number (coexistence) finishes with
  // its own event; a cancel says at which step, so the card can say what happened.
  const sessionInfo = useRef(null)
  const cancelled = useRef(null)
  useEffect(() => {
    const onMessage = (event) => {
      try {
        if (!/facebook\.com$/.test(new URL(event.origin).hostname)) return
        const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data
        if (msg?.type !== 'WA_EMBEDDED_SIGNUP') return
        if (String(msg.event || '').startsWith('FINISH') && msg.data) {
          sessionInfo.current = { waba_id: msg.data.waba_id, phone_number_id: msg.data.phone_number_id,
            ...(msg.event === 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING' ? { coexistence: true } : {}) }
        } else if (msg.event === 'CANCEL') {
          cancelled.current = msg.data?.error_message || msg.data?.current_step || null
        }
      } catch { /* not a signup message */ }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  // Load Meta Facebook SDK
  useEffect(() => {
    if (!FB_APP_ID || window.FB) return
    const script = document.createElement('script')
    script.src = 'https://connect.facebook.net/en_US/sdk.js'
    script.async = true
    script.defer = true
    script.crossOrigin = 'anonymous'
    document.head.appendChild(script)
    window.fbAsyncInit = () => {
      window.FB.init({
        appId: FB_APP_ID,
        autoLogAppEvents: true,
        xfbml: true,
        version: 'v19.0',
      })
    }
  }, [])

  // mode 'app': keep the WhatsApp Business app number the business already uses (Meta's
  // coexistence: the app stays on the owner's phone, Vula answers on the same number). mode 'new':
  // a number that isn't on WhatsApp yet.
  // extra: { move_phone_number_id } to move a number from Vula's WhatsApp account to the owner's own.
  const handleConnect = useCallback((mode = 'app', purpose = null, extra = null) => {
    if (!FB_CONFIG_ID) {
      setError("WhatsApp signup isn't set up on this dashboard yet — ask Vula to link the number for you.")
      return
    }
    if (!window.FB) {
      setError('Facebook SDK not loaded. Please refresh and try again.')
      return
    }

    setLoading(true)
    setError(null)
    setNotice(null)
    sessionInfo.current = null
    cancelled.current = null

    window.FB.login(
      async (response) => {
        if (!response.authResponse) {
          setLoading(false)
          setError(cancelled.current ? `Connection stopped: ${cancelled.current}` : 'Connection cancelled.')
          return
        }

        try {
          const code = response.authResponse.code

          const result = await fetch(`${VULA_API}/v1/whatsapp/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              tenant_id: tenantId,
              code,
              connected_by: adminEmail,
              ...(sessionInfo.current || {}),
              // Meta's own finish event says which it was; the chosen option only when it didn't say
              coexistence: sessionInfo.current ? Boolean(sessionInfo.current.coexistence) : mode === 'app',
              ...(purpose ? { purpose } : {}),
              ...(extra || {}),
            }),
          })

          if (!result.ok) {
            const err = await result.json()
            throw new Error(err.detail || 'Connection failed')
          }

          const data = await result.json()
          setAccount(data)
          setStatus('connected')
          if (data.move_note) setNotice(data.move_note)
          reload()                                  // the full list, with the new number in it
        } catch (err) {
          setError(err.message)
          setStatus('error')
        } finally {
          setLoading(false)
        }
      },
      {
        config_id: FB_CONFIG_ID,
        response_type: 'code',
        override_default_response_type: true,
        // Embedded Signup v4 (v2 and v3 were retired on 15 Oct 2026): the version and products
        // come from the configuration (VITE_FB_CONFIG_ID); the extras pick the flow.
        extras: {
          setup: {},
          featureType: mode === 'app' ? 'whatsapp_business_app_onboarding' : '',
          sessionInfoVersion: '3',
        },
      }
    )
  }, [tenantId, adminEmail, reload])

  const handleDisconnect = async () => {
    if (!(await confirmDialog(`Disconnect WhatsApp for ${tenantName}? Ordering will stop immediately.`, { danger: true, confirmLabel: 'Yes' }))) return
    setLoading(true)
    await fetch(`${VULA_API}/v1/whatsapp/disconnect/${tenantId}`, { method: 'DELETE' })
    setStatus('not_connected')
    setAccount(null)
    setLoading(false)
  }

  // One number: what it's for, its name, or make it the main one.
  const updateNumber = async (pnid, patch) => {
    setBusyNumber(pnid)
    setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/whatsapp/numbers/${tenantId}/${pnid}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Couldn't save that just now.")
      await reload()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyNumber(null)
    }
  }

  const disconnectNumber = async (n) => {
    const others = numbersOf(account).filter(m => m.phone_number_id !== n.phone_number_id && m.status === 'connected')
    const msg = others.length
      ? `Disconnect ${n.phone_number} from Vula? Messages to it stop being answered.${n.is_primary ? ' Another of your numbers becomes the main one.' : ''}`
      : `Disconnect WhatsApp for ${tenantName}? Ordering will stop immediately.`
    if (!(await confirmDialog(msg, { danger: true, confirmLabel: 'Yes' }))) return
    setBusyNumber(n.phone_number_id || 'all')
    const q = n.phone_number_id ? `?phone_number_id=${encodeURIComponent(n.phone_number_id)}` : ''
    await fetch(`${VULA_API}/v1/whatsapp/disconnect/${tenantId}${q}`, { method: 'DELETE' })
    setBusyNumber(null)
    if (others.length) { await reload(); return }
    setStatus('not_connected')
    setAccount(null)
  }

  return { status, account, numbers: numbersOf(account), health, loading, error, notice, busyNumber,
    handleConnect, handleDisconnect, updateNumber, disconnectNumber }
}
