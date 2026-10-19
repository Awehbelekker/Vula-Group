/**
 * useYocoConnect.js — Yoco API keys: connect (validated by the backend, optional webhook
 * auto-registration), test, update, disconnect (2026-10-19, UX redesign Phase F). Moved out of
 * VulaYocoConnect unchanged — same /v1/yoco/{status,connect,test,disconnect} calls.
 */
import { useEffect, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog, toast } from '../../ui/index.jsx'

export function useYocoConnect({ tenantId, tenantName, adminEmail }) {
  const [status, setStatus] = useState(null)
  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)

  const [form, setForm] = useState({
    secret_key: '',
    public_key: '',
    auto_register_webhook: true,
  })

  useEffect(() => {
    if (!tenantId) return
    fetch(`${VULA_API}/v1/yoco/status/${tenantId}`)
      .then(r => r.json())
      .then(data => {
        setStatus(data.status)
        setAccount(data)
      })
      .catch(() => setStatus('error'))
  }, [tenantId])

  async function handleConnect(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${VULA_API}/v1/yoco/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: tenantId,
          secret_key: form.secret_key.trim(),
          public_key: form.public_key.trim() || null,
          auto_register_webhook: form.auto_register_webhook,
          connected_by: adminEmail,
        }),
      })
      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || 'Connection failed')
      }
      const data = await resp.json()
      setAccount({
        status: data.status,
        mode: data.mode,
        webhook_registered: data.webhook_registered,
        masked_secret: data.masked_secret,
        public_key: form.public_key,
        last_test_at: new Date().toISOString(),
      })
      setStatus('connected')
      setShowForm(false)
      setForm({ secret_key: '', public_key: '', auto_register_webhook: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    try {
      const resp = await fetch(`${VULA_API}/v1/yoco/test/${tenantId}`, { method: 'POST' })
      const data = await resp.json()
      if (!data.ok) setError(`Test failed: HTTP ${data.status_code}`)
      else { setError(null); toast('✓ Yoco keys valid') }
    } catch (err) {
      setError(err.message)
    } finally {
      setTesting(false)
    }
  }

  async function handleDisconnect() {
    if (!(await confirmDialog(`Disconnect Yoco for ${tenantName}? Pending checkouts will fail.`, { danger: true, confirmLabel: 'Yes' }))) return
    setLoading(true)
    await fetch(`${VULA_API}/v1/yoco/disconnect/${tenantId}`, { method: 'DELETE' })
    setStatus('not_connected')
    setAccount(null)
    setLoading(false)
  }

  return { status, account, loading, testing, error, showForm, setShowForm, form, setForm, handleConnect, handleTest, handleDisconnect }
}
