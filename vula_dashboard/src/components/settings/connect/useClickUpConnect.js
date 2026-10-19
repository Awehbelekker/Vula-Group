/**
 * useClickUpConnect.js — ClickUp: OAuth popup (opened inside the tap), the personal-token way
 * round, "Send missed documents" and the default list (2026-10-19, UX redesign Phase F). Moved
 * out of VulaClickUpConnect unchanged — same /v1/clickup/{status,lists,authorize-url,connect,
 * refile-missing,default-list} calls.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'

export function useClickUpConnect(tenantId) {
  const [status, setStatus] = useState(null)   // null | connected | not_connected | error
  const [account, setAccount] = useState(null)
  const [lists, setLists] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)
  const [showToken, setShowToken] = useState(false)
  const [token, setToken] = useState('')
  const [notice, setNotice] = useState(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/clickup/status/${tenantId}`)
      const d = await r.json()
      setStatus(d.status)
      setAccount(d)
      if (d.status === 'connected') {
        const lr = await fetch(`${VULA_API}/v1/clickup/lists/${tenantId}`)
        const ld = await lr.json()
        setLists(ld.lists || [])
      }
    } catch {
      setStatus('error')
    }
  }, [tenantId])

  useEffect(() => { loadStatus() }, [loadStatus])

  // Re-poll when the popup posts back, on window focus, and briefly on an interval.
  useEffect(() => {
    const onMsg = (e) => { if (e.data === 'clickup-connected') loadStatus() }
    const onFocus = () => loadStatus()
    window.addEventListener('message', onMsg)
    window.addEventListener('focus', onFocus)
    return () => {
      window.removeEventListener('message', onMsg)
      window.removeEventListener('focus', onFocus)
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [loadStatus])

  const handleConnect = useCallback(async () => {
    setLoading(true)
    setError(null)
    // Open the window NOW, inside the tap: a window opened after an `await` isn't a user action
    // any more, and phones and Safari silently block it (2026-09-29, Judy couldn't reconnect).
    // If it's blocked anyway, go to ClickUp in this tab; the callback brings her back.
    const popup = window.open('', 'clickup-oauth', 'width=620,height=760')
    try {
      const r = await fetch(`${VULA_API}/v1/clickup/authorize-url?tenant_id=${encodeURIComponent(tenantId)}`)
      const d = await r.json()
      if (!d.url) throw new Error(d.error || 'ClickUp app not configured.')
      if (popup && !popup.closed) popup.location.href = d.url
      else { window.location.href = d.url; return }
      // Poll for ~90s while the user authorises in the popup.
      let ticks = 0
      pollRef.current = setInterval(() => {
        loadStatus()
        if (++ticks > 30 || status === 'connected') clearInterval(pollRef.current)
      }, 3000)
    } catch (err) {
      if (popup && !popup.closed) popup.close()
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [tenantId, loadStatus, status])

  const connectWithToken = async () => {
    setLoading(true); setError(null); setNotice(null)
    try {
      const r = await fetch(`${VULA_API}/v1/clickup/connect`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId, api_token: token.trim() }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'ClickUp didn\'t accept that token.')
      setToken(''); setShowToken(false)
      setNotice(`Connected to ${d.workspace || 'ClickUp'} — documents filed while it was off are being sent across now.`)
      loadStatus()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const refileMissing = async () => {
    setError(null); setNotice(null)
    const r = await fetch(`${VULA_API}/v1/clickup/refile-missing/${tenantId}`, { method: 'POST' })
    const d = await r.json().catch(() => ({}))
    if (r.ok) setNotice('Sending documents that never reached ClickUp — this runs in the background.')
    else setError(d.detail || 'Could not start that.')
  }

  const setDefaultList = async (listId) => {
    await fetch(`${VULA_API}/v1/clickup/default-list`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_id: tenantId, list_id: listId }),
    })
    loadStatus()
  }

  return { status, account, lists, loading, error, notice, showToken, setShowToken, token, setToken,
    handleConnect, connectWithToken, refileMissing, setDefaultList }
}
