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
const FB_CONFIG_ID = import.meta.env.VITE_FB_CONFIG_ID || ''

export function useWhatsAppConnect({ tenantId, tenantName, adminEmail }) {
  const [status, setStatus] = useState(null)   // null | 'connected' | 'pending' | 'error'
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

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

  // Embedded Signup (sessionInfoVersion 2) posts the WhatsApp account + number the owner picked
  // as a window message. Sending those to the backend connects THAT number — before, the first
  // number found on any of the owner's businesses was used.
  const sessionInfo = useRef(null)
  useEffect(() => {
    const onMessage = (event) => {
      try {
        if (!/facebook\.com$/.test(new URL(event.origin).hostname)) return
        const msg = typeof event.data === 'string' ? JSON.parse(event.data) : event.data
        if (msg?.type === 'WA_EMBEDDED_SIGNUP' && msg.event === 'FINISH' && msg.data) {
          sessionInfo.current = { waba_id: msg.data.waba_id, phone_number_id: msg.data.phone_number_id }
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

  const handleConnect = useCallback(() => {
    if (!window.FB) {
      setError('Facebook SDK not loaded. Please refresh and try again.')
      return
    }

    setLoading(true)
    setError(null)

    window.FB.login(
      async (response) => {
        if (!response.authResponse) {
          setLoading(false)
          setError('Connection cancelled.')
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
        extras: {
          setup: {},
          featureType: '',
          sessionInfoVersion: '2',
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

  return { status, account, loading, error, handleConnect, handleDisconnect }
}
