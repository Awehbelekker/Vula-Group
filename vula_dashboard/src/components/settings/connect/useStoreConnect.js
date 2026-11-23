/**
 * useStoreConnect.js — connect an online store, read its catalogue, keep it in step: Shopify and
 * WooCommerce (2026-10-19, UX redesign Phase F). Moved out of VulaShopifyConnect /
 * VulaWooConnect, whose two copies differed only in the path, the form and the wording; the calls
 * are unchanged: GET /admin/{path}, POST /admin/{path}/connect, POST /admin/{path}/sync,
 * DELETE /admin/{path} (vula/integrations/shopify.py, woocommerce.py). Polls while a sync runs.
 */
import { useEffect, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { errorText } from '../../../lib/api'
import { confirmDialog, toast } from '../../ui/index.jsx'

export const STORES = {
  shopify: {
    path: 'shopify', label: 'Shopify',
    emptyForm: { shop_domain: '', client_id: '', client_secret: '', access_token: '' },
    bodyOf: (form, useToken) => useToken
      ? { shop_domain: form.shop_domain, access_token: form.access_token }
      : { shop_domain: form.shop_domain, client_id: form.client_id, client_secret: form.client_secret },
    disconnectMsg: 'Disconnect Shopify? Your products stay in Vula, but they stop updating from Shopify.',
  },
  woo: {
    path: 'woocommerce', label: 'WooCommerce',
    emptyForm: { store_url: '', consumer_key: '', consumer_secret: '' },
    bodyOf: (form) => form,
    disconnectMsg: 'Disconnect WooCommerce? Your products stay in Vula, but they stop updating from your shop.',
  },
}

export function useStoreConnect({ tenantId, adminEmail, store }) {
  const S = STORES[store]
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/${S.path}`
  const [info, setInfo] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [useToken, setUseToken] = useState(false)
  const [form, setForm] = useState(S.emptyForm)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function load() {
    try {
      const r = await fetch(base)
      if (!r.ok) toast(`Couldn't check ${S.label}: ${errorText(await r.json().catch(() => null))}`, 'danger')
      setInfo(r.ok ? await r.json() : { connected: false })
    } catch {
      toast(`Couldn't check ${S.label}. Check your connection and try again.`, 'danger')
      setInfo({ connected: false })
    }
  }

  useEffect(() => { if (tenantId) load() }, [tenantId])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!info?.sync?.running) return
    const t = setTimeout(load, 4000)
    return () => clearTimeout(t)
  }, [info])   // eslint-disable-line react-hooks/exhaustive-deps

  async function connect(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const body = S.bodyOf(form, useToken)
      const r = await fetch(`${base}/connect`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, connected_by: adminEmail }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(errorText(data, `${S.label} connection failed`))
      setForm(S.emptyForm)
      setShowForm(false)
      toast(`${S.label} connected — reading your catalogue now.`)
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function syncNow() {
    const r = await fetch(`${base}/sync`, { method: 'POST' }).catch(() => null)
    const data = r ? await r.json().catch(() => ({})) : {}
    if (!r || !r.ok) { toast(errorText(data, 'Could not start the sync'), 'danger'); return }
    toast(data.started ? `Reading your ${S.label} catalogue…` : 'A sync is already running.')
    load()
  }

  async function disconnect() {
    if (!(await confirmDialog(S.disconnectMsg))) return
    const r = await fetch(base, { method: 'DELETE' }).catch(() => null)
    if (!r || !r.ok) {
      const d = r ? await r.json().catch(() => null) : null
      toast(`${S.label} wasn't disconnected: ${r ? errorText(d) : "Couldn't reach Vula. Check your connection and try again."}`, 'danger')
      return
    }
    toast(`${S.label} disconnected.`)
    load()
  }

  return { info, showForm, setShowForm, useToken, setUseToken, form, setForm, busy, error, setError, connect, syncNow, disconnect }
}
