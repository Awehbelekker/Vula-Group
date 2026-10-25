/**
 * useWhatsAppConnect.js — connect a WhatsApp Business number through Meta Embedded Signup
 * (2026-10-19, UX redesign Phase F). Moved out of VulaWhatsAppConnect unchanged: loads the
 * Facebook SDK, takes the WABA + number the owner picked from the signup's window message, sends
 * the code to POST /v1/whatsapp/connect; status from /v1/whatsapp/connect/status, disconnect via
 * DELETE /v1/whatsapp/disconnect.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog } from '../../ui/index.jsx'

export const FB_APP_ID = import.meta.env.VITE_FB_APP_ID || ''
// Meta's Embedded Signup configuration. Without it FB.login opens nothing useful and the button
// used to hang on "Connecting…" — so Connect stays off and the card says why (2026-10-24).
export const FB_CONFIG_ID = import.meta.env.VITE_FB_CONFIG_ID || ''

export function useWhatsAppConnect({ tenantId, tenantName, adminEmail }) {
  const [status, setStatus] = useState(null)   // null | 'connected' | 'pending' | 'error'
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [health, setHealth] = useState(null)   // { rows: [{label, value, tone}], error? }

  // Load current connection status
  useEffect(() => {
    if (!tenantId) return
    fetch(`${VULA_API}/v1/whatsapp/connect/status/${tenantId}`)
      .then(r => r.json())
      .then(data => {
        setStatus(data.status)
        setAccount(data)
      })
      .catch(() => setStatus('error'))
  }, [tenantId])

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
  const handleConnect = useCallback((mode = 'app') => {
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
            }),
          })

          if (!result.ok) {
            const err = await result.json()
            throw new Error(err.detail || 'Connection failed')
          }

          const data = await result.json()
          setAccount(data)
          setStatus('connected')
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
  }, [tenantId, adminEmail])

  const handleDisconnect = async () => {
    if (!(await confirmDialog(`Disconnect WhatsApp for ${tenantName}? Ordering will stop immediately.`, { danger: true, confirmLabel: 'Yes' }))) return
    setLoading(true)
    await fetch(`${VULA_API}/v1/whatsapp/disconnect/${tenantId}`, { method: 'DELETE' })
    setStatus('not_connected')
    setAccount(null)
    setLoading(false)
  }

  return { status, account, health, loading, error, handleConnect, handleDisconnect }
}
