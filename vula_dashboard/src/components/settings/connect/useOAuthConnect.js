/**
 * useOAuthConnect.js — popup OAuth → backend callback → status poll, for Google and Microsoft
 * (2026-10-19, UX redesign Phase F). Moved out of VulaGoogleConnect / VulaMicrosoftConnect, whose
 * two copies differed only in names; the calls are unchanged:
 *   GET /v1/{provider}/status/{tenant}, GET /v1/{provider}/authorize-url?tenant_id=…
 * The popup posts `{provider}-connected` back; window focus re-polls too.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'

/** provider: "google" | "microsoft"; popupName: the window name; label: for the error message. */
export function useOAuthConnect({ tenantId, provider, popupName, label }) {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/${provider}/status/${tenantId}`)
      const d = await r.json()
      setStatus(d.status); setAccount(d)
    } catch { setStatus('error') }
  }, [tenantId, provider])

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => {
    const onMsg = (e) => { if (e.data === `${provider}-connected`) loadStatus() }
    const onFocus = () => loadStatus()
    window.addEventListener('message', onMsg); window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('message', onMsg); window.removeEventListener('focus', onFocus); if (pollRef.current) clearInterval(pollRef.current) }
  }, [loadStatus, provider])

  const handleConnect = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/${provider}/authorize-url?tenant_id=${encodeURIComponent(tenantId)}`)
      const d = await r.json()
      if (!d.url) throw new Error(d.error || `${label} app not configured.`)
      window.open(d.url, popupName, 'width=520,height=680')
      let ticks = 0
      pollRef.current = setInterval(() => { loadStatus(); if (++ticks > 30 || status === 'connected') clearInterval(pollRef.current) }, 3000)
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }, [tenantId, provider, popupName, label, loadStatus, status])

  return { status, account, loading, error, handleConnect }
}

/** "5m ago" / "3h ago" / "2d ago" for a sync time, or null. */
export function timeAgo(iso) {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(ms) || ms < 0) return null
  const mins = Math.round(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}
